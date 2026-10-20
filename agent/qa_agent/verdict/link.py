"""Link against outcome-free families so observed defects cannot bias coverage."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from ..llm.base import LLMUnavailable
from ..models import RequirementCatalogue, Testability
from ..verify.models import Check
from .models import AssertionLink, Link, LinkResult

SYSTEM_PROMPT = '''You are a QA coverage analyst. Link every requirement exactly once to the supplied check families.
Use only supplied family tags, optionally followed by a check-id suffix filter from that family's suffixes,
e.g. transfer.record.memo or transfer.same_account.explanation_quality.
A linked family must test the behaviour the requirement states, not merely touch the same data: a requirement
about what seed data exists links to families that inspect the data, not to families that test a filter.
Requirements about wording or clarity link to families with has_judge true (their judge suffixes); they are not
unlinkable because they need judgement.
For every requirement also list, per assertion (index = position in its assertions list, 0-based), the families
that test that assertion; leave an assertion's families empty when no family tests it. Coverage is recomputed
from that mapping, so claim full only if every assertion has a family. Do not infer observed outcomes.
Not-testable requirements have no families and coverage none. Give a one-line rationale. Return schema JSON.'''
USER_TEMPLATE = 'Requirements:\n{requirements}\n\nCheck families (examples contain only id and expected):\n{families}'


def key_for(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def matches(check: Check, family: str) -> bool:
    if family == check.behaviour:
        return True
    prefix = check.behaviour + '.'
    return family.startswith(prefix) and check.id.endswith('.' + family[len(prefix):])


def family_catalogue(checks: list[Check]) -> dict:
    """Outcome-free description of each family: examples, the suffixes usable as filters, judge presence."""
    catalogue = {}
    for family in sorted({c.behaviour for c in checks}):
        members = [c for c in checks if c.behaviour == family]
        # Last id segment is the usable filter; purely numeric segments (row/case indexes) are not filters.
        suffixes = sorted({seg for c in members if not (seg := c.id[len(family) + 1:].split('.')[-1]).isdigit()})
        catalogue[family] = dict(examples=[dict(id=c.id, expected=c.expected) for c in members][:3],
                                 suffixes=suffixes[:12], has_judge=any(c.needs_judge for c in members))
    return catalogue


def validate(result: LinkResult, catalogue: RequirementCatalogue, checks: list[Check]) -> LinkResult:
    warnings = list(result.warnings)
    requirements = {r.id: r for r in catalogue.requirements}
    known = set(requirements)
    by_id = {}
    for link in result.links:
        if link.requirement_id not in known or link.requirement_id in by_id:
            warnings.append(f'{link.requirement_id}: unknown or duplicate requirement dropped')
            continue
        def valid(names: list[str], rid: str = link.requirement_id) -> list[str]:
            kept = []
            for family in dict.fromkeys(names):
                if any(matches(c, family) for c in checks):
                    kept.append(family)
                else:
                    warnings.append(f'{rid}: unknown family {family} dropped')
            return kept

        req = requirements[link.requirement_id]
        per_assertion: dict[int, list[str]] = {}
        for a in link.assertions:
            if 0 <= a.index < len(req.assertions):
                per_assertion.setdefault(a.index, []).extend(valid(a.families))
        families = list(dict.fromkeys(valid(link.families) + [f for fs in per_assertion.values() for f in fs]))
        # Coverage is never taken on the model's word: "full" needs every assertion mapped to an existing family.
        mapped = sum(1 for i in range(len(req.assertions)) if per_assertion.get(i))
        computed = ('none' if not families else
                    'full' if req.assertions and mapped == len(req.assertions) else 'partial')
        coverage = min(computed, link.coverage, key=('none', 'partial', 'full').index)
        if families and coverage == 'none':
            coverage = 'partial'
        by_id[link.requirement_id] = link.model_copy(update={
            'families': families, 'coverage': coverage,
            'assertions': [AssertionLink(index=i, families=fs) for i, fs in sorted(per_assertion.items())]})
    links = []
    for req in catalogue.requirements:
        link = by_id.get(req.id)
        if link is None:
            warnings.append(f'{req.id}: missing requirement filled as none')
            link = Link(requirement_id=req.id, rationale='Missing from link response')
        if req.testability == Testability.NOT_TESTABLE:
            link = link.model_copy(update={'families': [], 'coverage': 'none'})
        links.append(link)
    return LinkResult(links=links, warnings=warnings)


def link_requirements(catalogue, checks, client, *, cache_dir: Path) -> LinkResult:
    requirements = [r.model_dump(mode='json') for r in catalogue.requirements]
    families = family_catalogue(checks)
    prompt = USER_TEMPLATE.format(requirements=json.dumps(requirements, sort_keys=True),
                                  families=json.dumps(families, sort_keys=True))
    key = key_for(requirements, families, client.model_for('planner'), client.provider,
                  SYSTEM_PROMPT, prompt, LinkResult.model_json_schema(), 'low')
    path = cache_dir / 'link' / f'{key}.json'
    if path.exists():
        return validate(LinkResult.model_validate_json(path.read_text()), catalogue, checks)
    response = client.generate(prompt, phase='link', tier='planner', thinking='low',
                               system=SYSTEM_PROMPT, schema=LinkResult)
    # An unparseable or empty answer still "validates" because every field has a default; treat it as an
    # outage (requirements become unverified) and never cache it, or every later run would reuse it.
    if response.parsed is None or not LinkResult.model_validate(response.parsed).links:
        raise LLMUnavailable(f'link response empty or not matching the schema ({response.model})')
    result = validate(LinkResult.model_validate(response.parsed), catalogue, checks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2))
    return result


STAGE1_CANDIDATES = 3   # families per assertion passed from the ranking choice to per-group verification
GROUP_ACCEPT = .80      # P(group tests the assertion) needed to link it
NONE_ACCEPT = .60       # ranking-choice P(none) that leaves an assertion unmapped instead of uncertain


def check_groups(checks: list[Check], family: str) -> dict[str, list[str]]:
    """Linkable groups of a family (tag → 'suffix: what each check verifies'). Numeric last segments are row/case
    indexes, so those checks group under the family tag itself."""
    groups: dict[str, set[str]] = {}
    for c in checks:
        if c.behaviour != family:
            continue
        seg = c.id[len(family) + 1:].split('.')[-1] if c.id.startswith(family + '.') else ''
        tag = family if not seg or seg.isdigit() else f'{family}.{seg}'
        groups.setdefault(tag, set()).add(f"{seg if tag != family else 'row'}: {c.expected}"[:160])
    return {tag: sorted(v)[:12] for tag, v in sorted(groups.items())}


# The checks are evidence, so they go in state as a list; the question only names the judgment.
GROUP_QUESTION = dict(type='noul', instructions=(
    'A QA agent ran automated checks. `item.family.scenarios` names the test scenarios that produced one check '
    "family, `item.family.checks` lists its checks (each is 'suffix: what the check verifies'), and "
    '`item.family.name` is the family tag. Do these checks directly test '
    'the behaviour stated in `item.assertion` (in the context of `item.requirement`)? Answer yes only if the '
    'checks verify that stated behaviour itself; checks that merely touch the same data while testing a different '
    'behaviour (for example a filter test for a statement about which data exists) do not count.'),
    criteria={'true': 'At least one listed check verifies the behaviour the assertion states.',
              'false': 'No listed check verifies the stated behaviour; they test something else.'})


def _family_of(tag: str, groups: dict[str, dict]) -> str:
    return tag if tag in groups else next(f for f, g in groups.items() if tag in g)


def link_requirements_jev(catalogue, checks, decider, client, *, cache_dir, scenarios: dict[str, str] | None = None):
    """Two stages: a choice over families ranks candidates (one choice over ~60 options spreads probability
    thin), then one noul per top family (and per judged wording group) decides the links. Binary judgments
    separate related from unrelated far more sharply than the ranking distribution."""
    from ..llm.typesafe import ask_many, DecisionUnavailable, EscalationClient
    families = family_catalogue(checks)
    groups = {family: check_groups(checks, family) for family in families}
    members = {tag: d for family_groups in groups.values() for tag, d in family_groups.items()}
    members.update({family: [d for ds in groups[family].values() for d in ds][:14] for family in families})
    judged = {tag for family in families for tag in groups[family]
              if any(c.needs_judge and matches(c, tag) for c in checks if c.behaviour == family)}
    # Scenario titles (our own probe descriptions) say what behaviour a family exercises; the terse expected
    # strings alone left correct families scoring ~0.6 in verification.
    titles = {family: sorted({(scenarios or {}).get(c.probe_id, c.probe_id) for c in checks if c.behaviour == family})[:4]
              for family in families}
    options = {family: (' / '.join(titles[family]) + ' — ' + '; '.join(members[family]))[:400] for family in families}
    options['none'] = 'No family tests this assertion'
    if len(options) > 254:
        if client is not None:
            return link_requirements(catalogue, checks, EscalationClient(client), cache_dir=cache_dir)
        raise DecisionUnavailable('Too many check family options')
    pending, requests, links, warnings = [], [], [], []
    for req in catalogue.requirements:
        if req.testability == Testability.NOT_TESTABLE:
            links.append(Link(requirement_id=req.id))
            continue
        state = {'requirement': dict(id=req.id, title=req.title, statement=req.statement,
                                     assertions=[a.description for a in req.assertions])}
        questions = {f'a{i}': dict(type='choice', instructions=(
            f'Which check family tests assertion `requirement.assertions[{i}]` of this requirement? '
            'Pick the family whose checks test the stated behaviour, not one that merely touches '
            'the same data; pick none if no family tests it.'), criteria=dict(options))
                     for i in range(len(req.assertions))}
        for question in questions.values():
            if len(json.dumps([state, question])) > 32000 * 4:
                question['criteria'] = {k: v[:120] for k, v in options.items()}
        pending.append(req)
        requests.append((state, questions))
    uncertain, succeeded = [], False
    ranked = ask_many(decider, requests, phase='link', cache_dir=cache_dir)
    # Stage 2: one noul request per (assertion, candidate). Candidates are each top family as a whole (an
    # assertion usually spans several of its groups, so one group covers it only partly) plus its judged wording
    # groups, so clarity requirements can link to the wording checks alone.
    plans, verify_requests = [], []
    for answer, req in zip(ranked, pending):
        plan = {}
        if not isinstance(answer, DecisionUnavailable):
            for i, assertion in enumerate(req.assertions):
                probabilities = answer[f'a{i}']['probabilities']
                top = sorted((k for k in probabilities if k != 'none' and k in groups),
                             key=lambda k: -probabilities[k])[:STAGE1_CANDIDATES]
                tags = list(dict.fromkeys(t for f in top for t in [f] + [g for g in groups[f] if g in judged]))
                plan[i] = [(len(verify_requests) + n, tag) for n, tag in enumerate(tags)]
                verify_requests.extend(({'item': dict(requirement=req.statement, assertion=assertion.description,
                                                      family=dict(name=tag, scenarios=titles[_family_of(tag, groups)],
                                                                  checks=members[tag]))}, {'tests': GROUP_QUESTION})
                                       for tag in tags)
        plans.append(plan)
    verified = ask_many(decider, verify_requests, phase='link', cache_dir=cache_dir) if verify_requests else []
    for req, answer, plan in zip(pending, ranked, plans):
        assertions, rationale, unsure = [], [], False
        failed = [verified[n] for names in plan.values() for n, _ in names if isinstance(verified[n], DecisionUnavailable)]
        if isinstance(answer, DecisionUnavailable) or failed:
            warnings.append(f'{req.id}: Jev link unavailable: {answer if isinstance(answer, Exception) else failed[0]}')
            unsure = True
        else:
            succeeded = True
            for i in range(len(req.assertions)):
                p_none = answer[f'a{i}']['probabilities'].get('none', 0)
                scored = [(tag, verified[n]['tests']['noul']) for n, tag in plan.get(i, [])]
                selected = [tag for tag, p in scored if p >= GROUP_ACCEPT]
                if not selected and p_none < NONE_ACCEPT:
                    unsure = True
                assertions.append(AssertionLink(index=i, families=selected))
                best = sorted(scored, key=lambda s: -s[1])[:3]
                rationale.append(f"a{i}: none={p_none:.2f} " + ', '.join(f'{t}={p:.2f}' for t, p in best))
        links.append(Link(requirement_id=req.id, assertions=assertions, coverage='full',
                          rationale='jev ' + '; '.join(rationale)))
        if unsure:
            uncertain.append(req)
    if uncertain and client is not None:
        try:
            subset = catalogue.model_copy(update={'requirements': uncertain})
            extra = link_requirements(subset, checks, EscalationClient(client), cache_dir=cache_dir)
            replacements = {link.requirement_id: link for link in extra.links}
            links = [replacements.get(link.requirement_id, link) for link in links]
            warnings.extend(extra.warnings)
            succeeded = True
        except Exception as exc:
            warnings.append(f'Link escalation unavailable: {exc}')
    if pending and not succeeded:
        raise DecisionUnavailable('No linking response from either provider')
    return validate(LinkResult(links=links, warnings=warnings), catalogue, checks)
