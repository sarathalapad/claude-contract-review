"""Human sign-off with a maker-checker rule, recorded in a hash-chained log.

Each audit entry embeds the hash of the previous one, so editing, deleting or
reordering any past decision breaks the chain and `verify` pinpoints where.
This is tamper-evident, not tamper-proof: anchor the latest hash somewhere
external (a ticket, a signed commit) for stronger guarantees.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from contractreview.models import ReviewResult

GENESIS = "0" * 64
Decision = Literal["accept", "reject"]


class SignoffError(Exception):
    pass


def _digest(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, event: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        entries = self.entries()
        entry = {
            "seq": len(entries) + 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "actor": actor,
            "payload": payload,
            "prev_hash": entries[-1]["hash"] if entries else GENESIS,
        }
        entry["hash"] = _digest(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, entry in enumerate(self.entries(), 1):
            if entry.get("seq") != i:
                return False, f"entry {i}: sequence number {entry.get('seq')} out of order"
            if entry.get("prev_hash") != prev:
                return False, f"entry {i}: prev_hash does not match entry {i - 1}"
            if _digest(entry) != entry.get("hash"):
                return False, f"entry {i}: content hash mismatch (entry was modified)"
            prev = entry["hash"]
        return True, f"{len(self.entries())} entries verified"


def record_review(log: AuditLog, result: ReviewResult) -> None:
    log.append(
        "review_completed",
        result.requested_by,
        {
            "contract": result.contract,
            "content_sha256": result.content_sha256,
            "reviewer": result.reviewer,
            "status": result.status,
            "risk_counts": result.risk_counts(),
        },
    )


def sign_off(
    result: ReviewResult,
    reviewer: str,
    decide: Callable[[Any], tuple[Decision, str]],
    log: AuditLog,
) -> dict[str, Decision]:
    """Collect accept/reject for every high-risk finding.

    Maker-checker: whoever requested the review may not approve it. Rejections
    need a reason so the audit trail explains why a flagged risk was waived.
    """
    if reviewer.strip().lower() == result.requested_by.strip().lower():
        raise SignoffError(f"maker-checker violation: '{reviewer}' requested this review and cannot sign it off")
    if result.status != "complete":
        raise SignoffError(f"review status is '{result.status}'; re-run the review before sign-off")
    decisions: dict[str, Decision] = {}
    for f in (f for f in result.sorted_findings() if f.risk == "high"):
        decision, reason = decide(f)
        if decision not in ("accept", "reject"):
            raise SignoffError(f"invalid decision {decision!r} for {f.id}")
        if decision == "reject" and not reason.strip():
            raise SignoffError(f"a reason is required to reject {f.id}")
        decisions[f.id] = decision
        log.append(
            "finding_decision",
            reviewer,
            {
                "contract": result.contract,
                "content_sha256": result.content_sha256,
                "finding_id": f.id,
                "rule_id": f.rule_id,
                "decision": decision,
                "reason": reason,
            },
        )
    log.append(
        "signoff_completed",
        reviewer,
        {
            "contract": result.contract,
            "content_sha256": result.content_sha256,
            "decisions": decisions,
        },
    )
    return decisions
