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
