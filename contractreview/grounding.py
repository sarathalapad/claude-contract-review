"""Grounding: keep only findings we can prove from the contract text.

LLM findings can cite clauses that do not exist or paraphrase text into
something the contract never says. Rather than trust the model, every finding
must survive these code-level checks before it reaches a report or a human.
"""

from __future__ import annotations

from collections.abc import Iterable

from contractreview.contract import Contract
from contractreview.models import RISK_ORDER, DroppedFinding, Finding
from contractreview.playbook import Playbook


def ground_findings(
    findings: Iterable[Finding],
    contract: Contract,
    playbook: Playbook,
    citation_spans: Iterable[tuple[int, int]] = (),
) -> tuple[list[Finding], list[DroppedFinding]]:
    spans = list(citation_spans)
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    seen: set[tuple[str, int | None]] = set()
    for f in findings:
        rule = playbook.get(f.rule_id)
        if rule is None:
            dropped.append(DroppedFinding(finding=f, reason=f"unknown playbook rule '{f.rule_id}'"))
            continue
        if not f.quote.strip():
            # A missing-clause claim has nothing to quote, so verify the
            # absence ourselves instead of taking the model's word for it.
            phrases = rule.check.get("any_of") if rule.check["type"] == "required_clause" else None
            if not phrases:
                dropped.append(DroppedFinding(finding=f, reason="no quote supplied for a non-missing-clause rule"))
            elif any(p in contract.text.lower() for p in phrases):
                dropped.append(DroppedFinding(finding=f, reason="claimed missing clause is present in the contract"))
            elif (f.rule_id, None) not in seen:
                seen.add((f.rule_id, None))
                kept.append(f.model_copy(update={"clause": "(missing)", "risk": rule.risk}))
            continue
        span = contract.find_quote(f.quote)
        if span is None:
            dropped.append(DroppedFinding(finding=f, reason="quote not found verbatim in the contract"))
            continue
        if (f.rule_id, span[0]) in seen:
            continue
        seen.add((f.rule_id, span[0]))
        sec = contract.section_at(span[0])
        kept.append(
            f.model_copy(
                update={
                    "quote": contract.text[span[0] : span[1]],
                    "start_char": span[0],
                    "end_char": span[1],
                    # Trust the character offset, not the model's clause label.
                    "clause": sec.label if sec else f.clause,
                    "cited": any(s < span[1] and span[0] < e for s, e in spans),
                }
            )
        )
    kept.sort(key=lambda f: (RISK_ORDER[f.risk], f.start_char if f.start_char is not None else -1))
    for i, f in enumerate(kept, 1):
        f.id = f"F{i:02d}"
    return kept, dropped
