from __future__ import annotations

from pathlib import Path

import pytest

from qa_agent.interpret import interpret, parse_prd, validate
from qa_agent.llm.base import LLMResponse
from qa_agent.models import (
    Area,
    Assertion,
    CostEntry,
    Exclusion,
    ExtractionResult,
    OracleType,
    PrdBullet,
    Requirement,
    ScenarioHint,
    ScenarioKind,
    Testability,
)

PRD = Path(__file__).resolve().parents[2] / "test-website" / "PRD.md"


def test_parse_real_prd_sections_and_context():
    parsed = parse_prd(PRD.read_text())
    assert parsed.title == "DemoBank Product Requirements"
    sections = {b.section for b in parsed.bullets}
    assert {"Users and access", "Transfers between owned accounts", "Transaction history", "Profile"} <= sections
    # Framing sections are context only, never normative bullets.
    assert "Out of scope" not in sections and "Out of scope" in parsed.context
    assert all(b.id == f"B{i:02d}" for i, b in enumerate(parsed.bullets, start=1))


def test_parse_strips_markdown_and_keeps_wrapped_items():
    parsed = parse_prd("# T\n\n## S\n\n- The **Enter Demo** action\n  continues here.\n- Second\n")
    assert [b.text for b in parsed.bullets] == ["The Enter Demo action continues here.", "Second"]
    assert parsed.bullets[0].line == 5


def _req(rid: str, area: Area, bullets: list[str], quote: str, **kw) -> Requirement:
    return Requirement(
        id=rid,
        area=area,
        title="t",
        statement="s",
        source_bullets=bullets,
        source_quote=quote,
        testability=kw.get("testability", Testability.AUTOMATED),
        assertions=kw.get("assertions", [Assertion(description="a", oracle=OracleType.UI_TEXT)]),
        scenario_hints=[ScenarioHint(kind=ScenarioKind.NORMAL, description="n")],
    )


BULLETS = [
    PrdBullet(id="B01", section="S", line=1, text="Logging out returns the user to the landing page."),
    PrdBullet(id="B02", section="S", line=2, text="All records are synthetic."),
]


def test_validate_accepts_covered_and_excluded():
    result = ExtractionResult(
        requirements=[_req("SES-001", Area.SES, ["B01"], "returns the user to the landing page")],
        excluded=[Exclusion(bullet_id="B02", reason="descriptive")],
    )
    assert validate(result, BULLETS) == []


def test_validate_flags_hallucinated_quote_bad_id_and_gap():
    result = ExtractionResult(
        requirements=[
            _req("SES-1", Area.SES, ["B01"], "returns the user to the dashboard"),
            _req("ACC-001", Area.SES, ["B09"], "x"),
        ]
    )
    problems = "\n".join(validate(result, BULLETS))
    assert "SES-1: id must be" in problems
    assert "not a verbatim excerpt" in problems
    assert "ACC-001: id must be" in problems and "unknown or missing source bullets ['B09']" in problems
    assert "B02: not covered" in problems


class FakeClient:
    provider = "fake"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.calls = 0

    def model_for(self, tier):
        return "fake-model"

    def generate(self, prompt, *, phase, tier="fast", system=None, schema=None, thinking=None):
        self.calls += 1
        return LLMResponse(
            text=self.result.model_dump_json(), parsed=self.result, model="fake-model",
            usage=CostEntry(phase=phase, model="fake-model"),
        )


def test_interpret_caches_by_prd_and_model(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# T\n\n## S\n\n- Logging out returns the user to the landing page.\n")
    client = FakeClient(
        ExtractionResult(requirements=[_req("SES-001", Area.SES, ["B01"], "Logging out returns the user")])
    )
    first = interpret(prd, client, cache_dir=tmp_path / "cache")
    second = interpret(prd, client, cache_dir=tmp_path / "cache")
    assert client.calls == 1
    assert first.requirements == second.requirements and first.warnings == []


def test_interpret_records_unrepaired_problems_as_warnings(tmp_path):
    prd = tmp_path / "prd.md"
    prd.write_text("# T\n\n## S\n\n- Alpha behavior.\n- Beta behavior.\n")
    client = FakeClient(ExtractionResult(requirements=[_req("SES-001", Area.SES, ["B01"], "Alpha behavior")]))
    catalogue = interpret(prd, client, cache_dir=tmp_path / "cache")
    assert client.calls == 2  # original + one repair attempt
    assert catalogue.warnings == ["B02: not covered by any requirement and not excluded"]


def test_validate_requires_judge_for_quality_wording():
    bullets = [PrdBullet(id="B01", section="S", line=1, text="Rejected with a clear explanation.")]
    plain = ExtractionResult(requirements=[_req("TRF-001", Area.TRF, ["B01"], "Rejected with")])
    assert any("no requirement from it has a judge assertion" in p for p in validate(plain, bullets))
    judged = ExtractionResult(requirements=[_req(
        "TRF-001", Area.TRF, ["B01"], "clear explanation",
        assertions=[Assertion(description="message explains why", oracle=OracleType.JUDGE)],
    )])
    assert validate(judged, bullets) == []


@pytest.mark.parametrize("quote", ["Logging out returns", "logging OUT returns the user"])
def test_quote_match_is_case_and_space_insensitive(quote):
    result = ExtractionResult(
        requirements=[_req("SES-001", Area.SES, ["B01"], quote)],
        excluded=[Exclusion(bullet_id="B02", reason="descriptive")],
    )
    assert validate(result, BULLETS) == []
