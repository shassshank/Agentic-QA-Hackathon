"""Build isolated DemoBank variants and evaluate recorded agent verdicts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

if __package__:
    from .score import aggregate, model_diff, score_run
else:
    from score import aggregate, model_diff, score_run

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT.parent / 'test-website'
OUTPUT = ROOT / 'runs/eval'
CATALOGUE = ROOT / 'runs/requirements.json'


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def resolve_variant(definitions, name, chain=()):
    if name in chain:
        raise ValueError('Variant inheritance cycle')
    own = next(v for v in definitions['variants'] if v['name'] == name)
    base = resolve_variant(definitions, own['base'], (*chain, name)) if own.get('base') else {}
    result = {**own, 'patches': base.get('patches', []) + own.get('patches', [])}
    for key in ('must_fail', 'may_fail'):
        result[key] = sorted(set(base.get(key, [])) | set(own.get(key, [])))
    result['own_must_fail'] = sorted(set(own.get('must_fail', [])) - set(base.get('must_fail', [])))
    return result


def definitions():
    data = read(ROOT / 'eval/variants.json')
    if data['catalogue_sha'] != read(CATALOGUE)['prd_sha256']:
        raise ValueError('Evaluation catalogue SHA mismatch')
    return data


def apply_patches(app, patches):
    applied = []
    for patch in patches:
        path = (app / patch['file']).resolve()
        if not path.is_relative_to(app.resolve()):
            raise ValueError('Patch outside app copy')
        text = path.read_text()
        matches = text.count(patch['find'])
        if not patch['find'] or patch['count'] < 1 or matches != patch['count']:
            raise ValueError(f"Patch count mismatch: {patch['file']}: expected {patch['count']}, got {matches}")
        path.write_text(text.replace(patch['find'], patch['replace']))
        applied.append({**patch, 'matches': matches})
    return applied


def prepare(name, frontend_port=5273):
    variant = resolve_variant(definitions(), name)
    folder = OUTPUT / name
    if (folder / 'pids.json').exists():
        raise ValueError('Stop the variant before preparing it')
    app = folder / 'app'
    if app.exists():
        shutil.rmtree(app)
    for part in ('backend', 'frontend'):
        shutil.copytree(TARGET / part, app / part,
                        ignore=shutil.ignore_patterns('node_modules', '.venv', '__pycache__', 'dist', '*.egg-info', '.env'))
    (app / 'frontend/node_modules').symlink_to(TARGET / 'frontend/node_modules', target_is_directory=True)
    patches = [{**p, 'replace': p['replace'].replace('http://localhost:5273', f'http://localhost:{frontend_port}')}
               for p in variant['patches']]
    applied = apply_patches(app, patches)
    write(folder / 'applied.json', dict(variant=name, catalogue_sha=definitions()['catalogue_sha'],
                                       frontend_port=frontend_port, patches=applied,
                                       sha256={p['file']: hashlib.sha256((app / p['file']).read_bytes()).hexdigest()
                                               for p in applied}))
    return app


def backend_env():
    env = os.environ.copy()
    # Never emit secrets; this backend uses simple KEY=value dotenv entries.
    path = TARGET / 'backend/.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.removeprefix('export ').partition('=')
            if sep:
                env[key.strip()] = value.strip().strip('\"\'')
    env.pop('PYTHONPATH', None)
    return env


def stop(name):
    path = OUTPUT / name / 'pids.json'
    if not path.exists():
        return
    pids = read(path)
    for pid in pids.values():
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):  # macOS: a reaped group can report EPERM
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        alive = []
        for pid in pids.values():
            try:
                os.killpg(pid, 0)
                alive.append(pid)
            except (ProcessLookupError, PermissionError):  # macOS: a reaped group can report EPERM
                pass
        if not alive:
            break
        time.sleep(.1)
    for pid in pids.values():
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):  # macOS: a reaped group can report EPERM
            pass
    path.unlink(missing_ok=True)


def serve(name, backend_port=8100, frontend_port=5273):
    folder = OUTPUT / name
    app = folder / 'app'
    manifest = read(folder / 'applied.json')
    if manifest['variant'] != name or manifest['frontend_port'] != frontend_port:
        raise ValueError('Prepare with matching variant and frontend port first')
    if manifest['catalogue_sha'] != definitions()['catalogue_sha']:
        raise ValueError('Prepared catalogue SHA mismatch')
    for file, digest in manifest['sha256'].items():
        if hashlib.sha256((app / file).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Prepared file changed: {file}')
    if (folder / 'pids.json').exists():
        raise ValueError('Variant already serving')
    if list(OUTPUT.glob('*/pids.json')):
        raise ValueError('Only one evaluation variant can serve at a time')
    # A listener answers a connect; a bind test would also reject ports in TIME_WAIT from the previous variant.
    for port in (backend_port, frontend_port):
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                raise OSError(f'Port {port} already has a listener')
    env = backend_env()
    python = str(TARGET / 'backend/.venv/bin/python')
    check = subprocess.run([python, '-c', 'import demobank; print(demobank.__file__)'], cwd=app / 'backend',
                           env=env, check=True, capture_output=True, text=True)
    if not Path(check.stdout.strip()).resolve().is_relative_to((app / 'backend').resolve()):
        raise ValueError('Backend import is outside the patched copy')
    write(folder / 'applied.json', {**manifest, 'backend_import': check.stdout.strip()})
    processes = {}
    try:
        commands = {
            'backend': [python, '-m', 'uvicorn', 'demobank.main:app', '--host', '127.0.0.1', '--port', str(backend_port)],
            'frontend': ['npm', 'run', 'dev', '--', '--host', 'localhost', '--port', str(frontend_port), '--strictPort'],
        }
        for part, command in commands.items():
            child_env = env if part == 'backend' else {**os.environ, 'VITE_API_BASE_URL': f'http://localhost:{backend_port}'}
            with (folder / f'{part}.log').open('w') as log:
                processes[part] = subprocess.Popen(command, cwd=app / part, env=child_env,
                                                   stdout=log, stderr=log, start_new_session=True)
            write(folder / 'pids.json', {key: process.pid for key, process in processes.items()})
        deadline = time.monotonic() + 45
        for url in (f'http://127.0.0.1:{backend_port}/health', f'http://localhost:{frontend_port}'):
            while True:
                if any(p.poll() is not None for p in processes.values()):
                    raise RuntimeError('Variant process exited; inspect variant logs')
                try:
                    with urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError('Variant startup timed out')
                time.sleep(.2)
    except BaseException:
        stop(name)
        raise


def reset_db():
    if list(OUTPUT.glob('*/pids.json')):
        raise ValueError('Stop the serving variant before resetting the shared database')
    subprocess.run(['make', '-C', str(TARGET / 'database'), 'reset'], check=True)


def run(name, models, backend_port, frontend_port):
    if list(OUTPUT.glob('*/pids.json')):
        raise ValueError('Stop the serving variant before resetting the shared database')
    reset_db()
    prepare(name, frontend_port)
    folder = OUTPUT / name
    run_dir = folder / 'run'
    # Every run* folder belongs to one prepared build; leftovers from an earlier build would be scored with this one.
    for old in folder.glob('run*'):
        if old.is_dir():
            shutil.rmtree(old)

    def agent(command, destination, env=None):
        args = ['uv', 'run', 'qa-agent', command]
        if command == 'probe':
            args += ['--url', f'http://localhost:{frontend_port}', '--out', str(destination)]
        else:
            args += ['--run', str(destination)]
        if command in ('verdict', 'report'):
            args += ['--catalogue', str(CATALOGUE)]
        subprocess.run(args, cwd=ROOT, env=env, check=True)

    try:
        serve(name, backend_port, frontend_port)
        agent('probe', run_dir)
        agent('verify', run_dir)
        # Preserve the original observations/checks before confirmation re-runs change them.
        snapshot = folder / 'snapshot'
        if snapshot.exists():
            shutil.rmtree(snapshot)
        snapshot.mkdir()
        for item in ('checks.json', 'observations.json', 'evidence'):
            source = run_dir / item
            if source.is_dir():
                shutil.copytree(source, snapshot / item)
            else:
                shutil.copy2(source, snapshot / item)
        agent('verdict', run_dir)
        agent('report', run_dir)
        for model in filter(None, models.split(',')):
            if '/' in model or model in ('.', '..'):
                raise ValueError('Model must be a directory-safe identifier')
            destination = folder / f'run-{model}'
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(snapshot, destination)
            agent('verdict', destination, {**os.environ, 'TYPESAFE_MODEL': model})
            agent('report', destination)
    finally:
        stop(name)


def score(names):
    data = definitions()
    results, differences = [], {}
    for name in names or [v['name'] for v in data['variants']]:
        variant = resolve_variant(data, name)
        reports = {}
        for path in sorted((OUTPUT / name).glob('run*/verdicts.json')):
            report = read(path)
            if report.get('catalogue', {}).get('prd_sha256', data['catalogue_sha']) != data['catalogue_sha']:
                raise ValueError(f'Run catalogue SHA mismatch: {path}')
            model = path.parent.name.removeprefix('run-')
            reports[model] = report
            def optional(file):
                return read(path.parent / file) if (path.parent / file).exists() else {}
            results.append(dict(variant=name, model=model, **score_run(
                variant, report, optional('cost.json'), optional('observations.json'))))
        differences[name] = model_diff(reports)
    models = sorted({r['model'] for r in results})
    metrics = dict(runs=results, overall=aggregate(results), model_differences=differences,
                   models={m: aggregate([r for r in results if r['model'] == m]) for m in models})
    write(OUTPUT / 'metrics.json', metrics)
    lines = ['| Variant | Model | TP | FP | False pass | Honest unverified | Precision | Recall |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in results:
        lines.append('| ' + ' | '.join(str(r[k]) for k in
                     ('variant', 'model', 'TP', 'FP', 'FN_pass', 'FN_unverified', 'precision', 'recall')) + ' |')
    lines += ['', '```json', json.dumps({k: v for k, v in metrics.items() if k != 'runs'}, indent=2), '```']
    (OUTPUT / 'metrics.md').write_text('\n'.join(lines) + '\n')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    choices = [v['name'] for v in definitions()['variants']]
    for command in ('prepare', 'serve', 'stop', 'run'):
        sub = commands.add_parser(command)
        sub.add_argument('variant', choices=choices)
        if command in ('prepare', 'serve', 'run'):
            sub.add_argument('--frontend-port', type=int, default=5273)
        if command in ('serve', 'run'):
            sub.add_argument('--backend-port', type=int, default=8100)
        if command == 'run':
            sub.add_argument('--jev-models', default='')
    commands.add_parser('reset-db')
    commands.add_parser('score').add_argument('--variants', nargs='+', choices=choices)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.variant, args.frontend_port)
    elif args.command == 'serve':
        serve(args.variant, args.backend_port, args.frontend_port)
    elif args.command == 'stop':
        stop(args.variant)
    elif args.command == 'reset-db':
        reset_db()
    elif args.command == 'run':
        run(args.variant, args.jev_models, args.backend_port, args.frontend_port)
    else:
        score(args.variants)


if __name__ == '__main__':
    main()
