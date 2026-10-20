"""Shared data models: PRD units, requirements, and LLM cost records."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Area(StrEnum):
    SES = "SES"  # session and access
    ACC = "ACC"  # dashboard and accounts
    TRF = "TRF"  # transfers
    HIS = "HIS"  # transaction history
    PRO = "PRO"  # profile
    GEN = "GEN"  # general interface behavior
    SEED = "SEED"  # seed data expectations


class Testability(StrEnum):
    __test__ = False  # not a pytest test class despite the name

    AUTOMATED = "automated"  # objective check against observable state
    JUDGEMENT = "judgement"  # needs a judge call (e.g. "clear explanation")
    NOT_TESTABLE = "not_testable"  # cannot be observed from the UI (reported unverified)


class OracleType(StrEnum):
    STATE_DIFF = "state_diff"  # before/after balances or records
    UI_TEXT = "ui_text"  # visible text, labels, formats
    INVARIANT = "invariant"  # every row satisfies a predicate
    NAVIGATION = "navigation"  # route or page reached
    NETWORK = "network"  # API responses observed in the browser
    LAYOUT = "layout"  # viewport usability
    JUDGE = "judge"  # subjective wording or clarity


class ScenarioKind(StrEnum):
    NORMAL = "normal"
    NEGATIVE = "negative"
    EDGE = "edge"


class PrdBullet(BaseModel):
    """One normative unit of the PRD: a list item or a prose paragraph, with its source line."""

    id: str
    section: str
    line: int
    text: str


class Assertion(BaseModel):
    description: str
    oracle: OracleType


class ScenarioHint(BaseModel):
    kind: ScenarioKind
    description: str


class Requirement(BaseModel):
    id: str = Field(description="AREA-NNN, e.g. TRF-003")
    area: Area
    title: str
    statement: str = Field(description="One atomic, verifiable behavior in plain words")
    source_bullets: list[str] = Field(description="Ids of the PRD bullets this requirement comes from")
    source_quote: str = Field(description="Verbatim excerpt from one of the source bullets")
    testability: Testability
    assertions: list[Assertion]
    scenario_hints: list[ScenarioHint]
    ambiguity: str | None = Field(default=None, description="Interpretation choice made, if the PRD is unclear")


class Exclusion(BaseModel):
    bullet_id: str
    reason: str


class ExtractionResult(BaseModel):
    """Schema the LLM must return."""

    requirements: list[Requirement]
    excluded: list[Exclusion] = Field(default_factory=list)


class RequirementCatalogue(BaseModel):
    prd_sha256: str
    model: str
    prompt_version: str
    generated_at: datetime
    bullets: list[PrdBullet]
    requirements: list[Requirement]
    excluded: list[Exclusion]
    warnings: list[str] = Field(default_factory=list, description="Validation issues left after repair")


class CostEntry(BaseModel):
    phase: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    cache_hit: bool = False
