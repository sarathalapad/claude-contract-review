"""Deterministic, rule-based reviewer used when no API key is available.

It emits exactly the same `Finding` schema as the Claude reviewer, which keeps
CI hermetic, gives a reproducible baseline to compare Claude against, and lets
the whole pipeline (grounding, reports, sign-off, audit) run with zero cost.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from contractreview.contract import Contract
from contractreview.grounding import ground_findings
from contractreview.models import Finding, ReviewResult
from contractreview.playbook import Playbook, Rule

Check = Callable[[Rule, Contract], list[Finding]]


def _finding(rule: Rule, contract: Contract, start: int, quote: str, issue: str) -> Finding:
    sec = contract.section_at(start)
    return Finding(
        rule_id=rule.id,
        clause=sec.label if sec else "(preamble)",
        risk=rule.risk,
        issue=issue,
        quote=quote,
        recommended_redline=rule.redline,
    )


def _triggered(rule: Rule, contract: Contract) -> list[tuple[int, int, str]]:
    trig = re.compile(rule.check["trigger"], re.IGNORECASE)
    return [s for s in contract.sentences() if trig.search(s[2])]


def check_required(rule: Rule, contract: Contract) -> list[Finding]:
    body = contract.text.lower()
    if any(p in body for p in rule.check["any_of"]):
        return []
    return [
        Finding(
            rule_id=rule.id,
            clause="(missing)",
            risk=rule.risk,
            issue=f"Required clause missing: {rule.title}.",
            recommended_redline=rule.redline,
        )
    ]


def check_forbidden(rule: Rule, contract: Contract) -> list[Finding]:
    pats = [re.compile(p, re.IGNORECASE) for p in rule.check["patterns"]]
    return [
        _finding(rule, contract, start, text, f"Forbidden term: {rule.title}.")
        for start, _, text in contract.sentences()
        if any(p.search(text) for p in pats)
    ]


def _check_numeric(rule: Rule, contract: Contract, *, want_min: bool) -> list[Finding]:
    value_re = re.compile(rule.check["value"], re.IGNORECASE)
    units: dict[str, float] = rule.check.get("units", {})
    limit = float(rule.check["min" if want_min else "max"])
    out = []
    for start, _, text in _triggered(rule, contract):
        for m in value_re.finditer(text):
            unit = (m.group(2) or "").lower() if value_re.groups >= 2 else ""
            value = float(m.group(1)) * units.get(unit, 1)
            if (value < limit) if want_min else (value > limit):
                bound = "below the minimum" if want_min else "above the maximum"
                out.append(
                    _finding(rule, contract, start, text, f"{rule.title}: found {value:g}, {bound} of {limit:g}.")
                )
                break
    return out


def check_location(rule: Rule, contract: Contract) -> list[Finding]:
    allowed, known = rule.check["allowed"], rule.check["known"]
    out = []
    for start, _, text in _triggered(rule, contract):
        bad = [loc for loc in known if loc not in allowed and loc in text.lower()]
        if bad:
            out.append(_finding(rule, contract, start, text, f"Data location outside UK/EEA: {', '.join(bad)}."))
    return out


def check_allowed_values(rule: Rule, contract: Contract) -> list[Finding]:
    return [
        _finding(rule, contract, start, text, f"{rule.title}: not one of {', '.join(rule.check['allowed'])}.")
        for start, _, text in _triggered(rule, contract)
        if not any(a in text.lower() for a in rule.check["allowed"])
    ]


CHECKS: dict[str, Check] = {
    "required_clause": check_required,
    "forbidden_pattern": check_forbidden,
    "numeric_min": lambda r, c: _check_numeric(r, c, want_min=True),
    "numeric_max": lambda r, c: _check_numeric(r, c, want_min=False),
    "location_allowlist": check_location,
    "allowed_values": check_allowed_values,
}


def review_offline(contract: Contract, playbook: Playbook, requested_by: str = "analyst") -> ReviewResult:
    raw: list[Finding] = []
    for rule in playbook.rules_for(contract.contract_type):
        raw.extend(CHECKS[rule.check["type"]](rule, contract))
    kept, dropped = ground_findings(raw, contract, playbook)
    counts = {k: sum(f.risk == k for f in kept) for k in ("high", "medium", "low")}
    return ReviewResult(
        contract=contract.name,
        contract_type=contract.contract_type,
        content_sha256=contract.sha256,
        reviewer="offline-rules",
        summary=(
            f"Rule-based review against {len(playbook.rules_for(contract.contract_type))} playbook rules: "
            f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low risk findings."
        ),
        findings=kept,
        dropped=dropped,
        requested_by=requested_by,
    )
