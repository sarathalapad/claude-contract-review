"""Pydantic schemas shared by the offline and Claude reviewers.

Both reviewers emit the same `Finding` shape so that grounding, reporting and
sign-off never need to know which engine produced a result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["high", "medium", "low"]
RISK_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


class Finding(BaseModel):
    """One playbook issue found in a contract."""

    rule_id: str
    clause: str = Field(description="Section label, e.g. '7. Limitation of Liability', or '(missing)'.")
    risk: RiskLevel
    issue: str
    quote: str = Field(default="", description="Verbatim contract text; empty only for missing-clause findings.")
    recommended_redline: str
    source: Literal["offline", "claude"] = "offline"
    id: str = ""
    start_char: int | None = None
    end_char: int | None = None
    cited: bool = Field(default=False, description="True when a Claude citation overlaps the quote.")


class DroppedFinding(BaseModel):
    """A finding rejected by grounding, kept for transparency in the report."""

    finding: Finding
    reason: str


class SubmittedFinding(BaseModel):
    """What Claude must send in the submit_review tool; validated before use."""

    rule_id: str
    clause: str
    risk: RiskLevel
    issue: str
    quote: str
    recommended_redline: str


class SubmittedReview(BaseModel):
    summary: str
    findings: list[SubmittedFinding]


class UsageReport(BaseModel):
    """Token and cost accounting summed over every API round of one review."""

    model: str
    rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0

    @property
    def cache_hit_ratio(self) -> float:
        total = self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens
        return self.cache_read_input_tokens / total if total else 0.0


class ReviewResult(BaseModel):
    contract: str
    contract_type: str
    content_sha256: str
    reviewer: str
    status: Literal["complete", "refused", "incomplete"] = "complete"
    status_detail: str = ""
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    dropped: list[DroppedFinding] = Field(default_factory=list)
    usage: UsageReport | None = None
    requested_by: str = "analyst"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def sorted_findings(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: (RISK_ORDER[f.risk], f.rule_id))

    def risk_counts(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for f in self.findings:
            counts[f.risk] += 1
        return counts
