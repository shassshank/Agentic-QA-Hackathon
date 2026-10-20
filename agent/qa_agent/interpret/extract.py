"""PRD → requirement catalogue: one cached LLM call, deterministic validation, at most one repair."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ..llm import LLMClient
from ..models import Area, ExtractionResult, PrdBullet, RequirementCatalogue, Testability
from .prd import ParsedPrd, clean_inline, parse_prd

PROMPT_VERSION = "interpret-v2"

SYSTEM_PROMPT = """You are a senior QA analyst. Convert a product requirements document into atomic,
verifiable requirements for an automated browser-based QA agent.

Rules:
- Each requirement states exactly one observable behavior. Split compound sentences: e.g. a bullet
  listing several invalid input classes yields one requirement per class, and a bullet listing several
  filter dimensions yields one requirement per dimension plus one for combining them.
- Ids are AREA-NNN with areas SES (session/access), ACC (dashboard/accounts), TRF (transfers),
  HIS (transaction history), PRO (profile), GEN (general interface), SEED (seed data). Number from 001
  per area in PRD order.
- source_bullets lists the bullet ids the requirement comes from. source_quote is copied verbatim from
  one of those bullets (a contiguous excerpt, no paraphrase).
- testability: "automated" when an objective check against visible UI state, URLs or browser network
  traffic can decide it; "judgement" when wording quality must be assessed (e.g. "clear explanation",
  "readable labels"); "not_testable" when it cannot be observed from the running web app (e.g. backend
  internals, absence of real payment rails).
  When a requirement mixes an objective part with a wording-quality part (e.g. "rejected with a clear
  explanation and must not alter balances"), keep testability "automated" and add a separate assertion
  with oracle "judge" for the wording.
- assertions: concrete checks with an oracle type (state_diff, ui_text, invariant, navigation, network,
  layout, judge). Prefer side-effect checks (balances, records, history) over "a message appeared".
- scenario_hints kinds: "normal" = a valid, allowed path; "negative" = an invalid input or forbidden
  action that must be rejected or blocked (e.g. same-account transfer, zero amount, access after
  logout); "edge" = a boundary value or limit (e.g. amount 0.01, exact balance, balance + 0.01,
  transactions on the start or end date, narrow viewport). Any requirement about rejection, blocking or
  "must not" needs at least one negative hint. Any requirement with a numeric or date boundary needs at
  least one edge hint.
- ambiguity: record any interpretation you had to choose; otherwise null.
- Every bullet must be covered by at least one requirement or listed in excluded with a reason. A
  normative statement ("must", "must never") that cannot be observed from the web app becomes a
  requirement with testability "not_testable", not an exclusion; exclude only purely descriptive or
  meta text. Do not invent behavior that the PRD does not state.
"""

_ID = re.compile(r"^(SES|ACC|TRF|HIS|PRO|GEN|SEED)-\d{3}$")


def _sha256(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _prompt(parsed: ParsedPrd) -> str:
    bullets = "\n".join(f"[{b.id}] ({b.section}, line {b.line}) {b.text}" for b in parsed.bullets)
    context = "\n\n".join(f"## {name}\n{text}" for name, text in parsed.context.items())
    return (
        f"PRD title: {parsed.title}\n\nNormative bullets:\n{bullets}\n\n"
        f"Context only (derive no requirements from these):\n{context}\n\n"
        "Return the requirement catalogue as JSON matching the schema."
    )


def validate(result: ExtractionResult, bullets: list[PrdBullet]) -> list[str]:
    """Deterministic checks that catch hallucinated sources, gaps and malformed ids."""
    problems: list[str] = []
    by_id = {b.id: b for b in bullets}
    seen: set[str] = set()
    covered: set[str] = set()
    for req in result.requirements:
        if not _ID.match(req.id) or not req.id.startswith(f"{req.area.value}-"):
            problems.append(f"{req.id}: id must be AREA-NNN matching area {req.area.value}")
        if req.id in seen:
            problems.append(f"{req.id}: duplicate id")
        seen.add(req.id)
        unknown = [s for s in req.source_bullets if s not in by_id]
        if unknown or not req.source_bullets:
            problems.append(f"{req.id}: unknown or missing source bullets {unknown}")
        sources = [by_id[s] for s in req.source_bullets if s in by_id]
        covered.update(b.id for b in sources)
        quote = clean_inline(req.source_quote).lower()
        if sources and not any(quote and quote in b.text.lower() for b in sources):
            problems.append(f"{req.id}: source_quote is not a verbatim excerpt of {req.source_bullets}")
        if req.testability != Testability.NOT_TESTABLE and not req.assertions:
            problems.append(f"{req.id}: testable requirement has no assertions")
    excluded = {e.bullet_id for e in result.excluded}
    for bullet in bullets:
        if bullet.id not in covered and bullet.id not in excluded:
            problems.append(f"{bullet.id}: not covered by any requirement and not excluded")
    return problems


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / "interpret" / f"{key}.json"


def interpret(
    prd_path: Path,
    client: LLMClient,
    *,
    cache_dir: Path,
    use_cache: bool = True,
) -> RequirementCatalogue:
    markdown = prd_path.read_text(encoding="utf-8")
    parsed = parse_prd(markdown)
    model = client.model_for("planner")
    prd_sha = _sha256(markdown)
    key = _sha256(markdown, model, PROMPT_VERSION, client.provider)
    cache_file = _cache_path(cache_dir, key)
    if use_cache and cache_file.exists():
        return RequirementCatalogue.model_validate_json(cache_file.read_text())

    prompt = _prompt(parsed)
    response = client.generate(
        prompt, phase="interpret", tier="planner", system=SYSTEM_PROMPT, schema=ExtractionResult, thinking="medium"
    )
    result: ExtractionResult | None = response.parsed
    problems = validate(result, parsed.bullets) if result else ["response did not match the schema"]

    if problems:
        repair_prompt = (
            f"{prompt}\n\nYour previous answer:\n{response.text}\n\nIt failed these checks:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\n\nReturn the full corrected catalogue."
        )
        repaired = client.generate(
            repair_prompt, phase="interpret-repair", tier="planner", system=SYSTEM_PROMPT,
            schema=ExtractionResult, thinking="medium",
        )
        if repaired.parsed is not None:
            result, response = repaired.parsed, repaired
            problems = validate(result, parsed.bullets)
    if result is None:
        raise RuntimeError("Interpreter returned no schema-valid catalogue after repair")

    catalogue = RequirementCatalogue(
        prd_sha256=prd_sha,
        model=response.model,
        prompt_version=PROMPT_VERSION,
        generated_at=datetime.now(UTC),
        bullets=parsed.bullets,
        requirements=sorted(result.requirements, key=lambda r: (list(Area).index(r.area), r.id)),
        excluded=result.excluded,
        warnings=problems,
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(catalogue.model_dump_json(indent=2))
    return catalogue


def save(catalogue: RequirementCatalogue, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalogue.model_dump(mode="json"), indent=2, ensure_ascii=False))
