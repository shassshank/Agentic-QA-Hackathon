"""Checks retain compact evidence references for review and later judging."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class Evidence(BaseModel):
    screenshots: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)


class Check(BaseModel):
    id: str
    behaviour: str
    area: str
    title: str
    status: Literal['pass', 'fail', 'unknown']
    reason: str | None = None
    expected: str
    observed: str
    probe_id: str
    evidence: Evidence = Field(default_factory=Evidence)
    needs_judge: bool = False
    judge_input: dict[str, Any] | None = None

    @model_validator(mode='after')
    def require_reason(self) -> Check:
        if self.status != 'pass' and not self.reason:
            raise ValueError('fail and unknown checks require a reason')
        return self


class CheckReport(BaseModel):
    run_tag: str
    base_url: str
    generated_at: str
    checks: list[Check]
    summary: dict[str, int]
