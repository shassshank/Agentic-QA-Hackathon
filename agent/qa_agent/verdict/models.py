"""Typed verdict boundaries keep uncertain evidence from becoming a pass."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

Coverage = Literal['full', 'partial', 'none']
Confirmation = Literal['confirmed', 'flaky', 'unconfirmed']
Reason = Literal['not_testable', 'no_linked_checks', 'partial_coverage', 'checks_unknown',
                 'needs_judgement', 'flaky', 'unconfirmed', 'probe_blocked', 'concurrent_activity',
                 'llm_unavailable', 'link_disputed']


class Link(BaseModel):
    requirement_id: str
    families: list[str] = Field(default_factory=list)
    coverage: Coverage = 'none'
    rationale: str = ''


class LinkResult(BaseModel):
    links: list[Link] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Judgement(BaseModel):
    item_id: str
    verdict: Literal['pass', 'fail', 'unsure']
    rationale: str


class JudgeResult(BaseModel):
    items: list[Judgement]


class Review(BaseModel):
    requirement_id: str
    verdict: Literal['violates', 'does_not_violate', 'unsure']
    rationale: str


class ReviewResult(BaseModel):
    items: list[Review]


class Verdict(BaseModel):
    requirement_id: str
    area: str
    title: str
    status: Literal['passed', 'failed', 'unverified']
    reason: Reason | None = None
    coverage: Coverage
    checks: list[dict[str, Any]]
    failure: dict[str, Any] | None = None
    review: dict[str, Any] | None = None  # failure-linkage review outcome, when a fail was reviewed
    unknown_checks: list[str] = Field(default_factory=list)
    matrix: dict[str, dict[str, int]] = Field(default_factory=dict)


class VerdictReport(BaseModel):
    run_tag: str
    catalogue: dict[str, str]
    verdicts: list[Verdict]
    summary: dict[str, int]
    cost: dict[str, Any]
    warnings: list[str]
