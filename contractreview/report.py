"""Markdown and JSON outputs. JSON is the machine record used by sign-off;
markdown is what a procurement lead actually reads."""

from __future__ import annotations

from pathlib import Path

from contractreview.models import ReviewResult

DISCLAIMER = "_Automated first-pass review against an internal playbook. Not legal advice; high-risk findings require human sign-off._"


def _cell(text: str, limit: int = 90) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def risk_table(results: list[ReviewResult]) -> str:
    rows = [
        "| Contract | Type | Reviewer | Status | High | Medium | Low | Dropped |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        c = r.risk_counts()
        rows.append(
            f"| {r.contract} | {r.contract_type} | {r.reviewer} | {r.status} | {c['high']} | {c['medium']} | {c['low']} | {len(r.dropped)} |"
        )
    return "\n".join(rows)


def to_markdown(r: ReviewResult) -> str:
    out = [
        f"# Contract review: {r.contract}",
        "",
        DISCLAIMER,
        "",
        f"- Contract type: `{r.contract_type}`",
        f"- Reviewer engine: `{r.reviewer}`",
        f"- Status: **{r.status}**" + (f" ({r.status_detail})" if r.status_detail else ""),
        f"- Content SHA-256: `{r.content_sha256[:16]}...`",
        "",
    ]
    if r.summary:
        out += ["## Summary", "", r.summary, ""]
    out += ["## Risk summary", "", risk_table([r]), "", "## Findings", ""]
    if not r.findings:
        out.append("No grounded findings.")
    else:
        out += ["| ID | Risk | Rule | Clause | Issue | Cited |", "|---|---|---|---|---|---|"]
        out += [
            f"| {f.id} | {f.risk.upper()} | {f.rule_id} | {_cell(f.clause, 40)} | {_cell(f.issue)} | {'yes' if f.cited else '-'} |"
            for f in r.findings
        ]
        for f in r.findings:
            out += ["", f"### {f.id} - {f.rule_id} ({f.risk})", "", f"**Clause:** {f.clause}", ""]
            if f.quote:
                out += [
                    f"> {' '.join(f.quote.split())}",
                    "",
                    f"Characters {f.start_char}-{f.end_char} of the source document.",
                    "",
                ]
            out += [f"**Issue:** {f.issue}", "", f"**Recommended redline:** {f.recommended_redline}"]
    if r.dropped:
        out += ["", "## Dropped (ungrounded) findings", ""]
        out += [f"- {d.finding.rule_id}: {d.reason}" for d in r.dropped]
    if r.usage:
        u = r.usage
        out += [
            "",
            "## Usage and cost",
            "",
            "| Rounds | Input | Output | Cache write | Cache read | Cache hit | Retries | Cost (USD) |",
            "|---|---|---|---|---|---|---|---|",
            f"| {u.rounds} | {u.input_tokens} | {u.output_tokens} | {u.cache_creation_input_tokens} | "
            f"{u.cache_read_input_tokens} | {u.cache_hit_ratio:.0%} | {u.retries} | ${u.cost_usd:.4f} |",
        ]
    return "\n".join(out) + "\n"


def write_outputs(r: ReviewResult, out_dir: str | Path) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    md, js = d / f"{r.contract}.review.md", d / f"{r.contract}.review.json"
    md.write_text(to_markdown(r), encoding="utf-8")
    js.write_text(r.model_dump_json(indent=2), encoding="utf-8")
    return md, js
