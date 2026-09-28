"""Command-line entry point: review, batch, signoff, verify-audit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from contractreview.audit import AuditLog, SignoffError, record_review, sign_off
from contractreview.contract import Contract
from contractreview.models import Finding, ReviewResult
from contractreview.offline import review_offline
from contractreview.playbook import Playbook
from contractreview.report import risk_table, write_outputs

DEFAULT_AUDIT = "audit/audit.jsonl"


def _use_live(args: argparse.Namespace) -> bool:
    # Offline is the default without a key, so a fresh clone and CI "just work".
    # --live forces the SDK, which can also resolve credentials from a profile.
    if args.offline:
        return False
    return bool(args.live or os.environ.get("ANTHROPIC_API_KEY"))


def _review_one(path: Path, args: argparse.Namespace, playbook: Playbook) -> ReviewResult:
    contract = Contract.load(path)
    if _use_live(args):
        from contractreview.claude_reviewer import ClaudeReviewer, ReviewConfig

        cfg = ReviewConfig(
            model=args.model, max_rounds=args.max_rounds, stream=args.stream, use_fallbacks=not args.no_fallback
        )
        on_text = (lambda t: print(t, end="", flush=True)) if args.stream else None
        result = ClaudeReviewer(playbook, config=cfg, on_text=on_text).review(contract, args.analyst)
        if args.stream:
            print()
    else:
        result = review_offline(contract, playbook, args.analyst)
    md, js = write_outputs(result, args.out)
    record_review(AuditLog(args.audit), result)
    c = result.risk_counts()
    print(
        f"{result.contract}: {result.status} via {result.reviewer} -> "
        f"{c['high']} high / {c['medium']} medium / {c['low']} low, {len(result.dropped)} dropped"
    )
    if result.usage:
        u = result.usage
        print(
            f"  tokens in={u.input_tokens} out={u.output_tokens} cache_write={u.cache_creation_input_tokens} "
            f"cache_read={u.cache_read_input_tokens} hit={u.cache_hit_ratio:.0%} cost=${u.cost_usd:.4f}"
        )
    print(f"  wrote {md.as_posix()} and {js.as_posix()}")
    return result


def cmd_review(args: argparse.Namespace) -> int:
    result = _review_one(Path(args.contract), args, Playbook.load(args.playbook))
    return 0 if result.status == "complete" else 2


def cmd_batch(args: argparse.Namespace) -> int:
    playbook = Playbook.load(args.playbook)
    paths = sorted(Path(args.directory).glob("*.md"))
    results = [_review_one(p, args, playbook) for p in paths]
    table = risk_table(results)
    Path(args.out, "summary.md").write_text("# Batch risk summary\n\n" + table + "\n", encoding="utf-8")
    print("\n" + table)
    return 0 if all(r.status == "complete" for r in results) else 2


def _interactive(f: Finding) -> tuple[str, str]:
    print(
        f"\n{f.id} [{f.risk.upper()}] {f.rule_id} in {f.clause}\n  Quote: {f.quote or '(missing clause)'}\n  Issue: {f.issue}"
    )
    while True:
        choice = input("  accept (a) / reject (r)? ").strip().lower()
        if choice in ("a", "accept"):
            return "accept", input("  note (optional): ")
        if choice in ("r", "reject"):
            return "reject", input("  reason (required): ")


def cmd_signoff(args: argparse.Namespace) -> int:
    result = ReviewResult.model_validate_json(Path(args.review_json).read_text(encoding="utf-8"))
    if args.decisions:
        table = json.loads(Path(args.decisions).read_text(encoding="utf-8"))

        def decide(f: Finding) -> tuple[str, str]:
            if f.id not in table:
                raise SignoffError(f"no decision supplied for {f.id}")
            d = table[f.id]
            return d["decision"], d.get("reason", "")
    else:
        decide = _interactive
    try:
        decisions = sign_off(result, args.reviewer, decide, AuditLog(args.audit))
    except SignoffError as exc:
        print(f"sign-off refused: {exc}", file=sys.stderr)
        return 1
    print(f"signed off {result.contract} by {args.reviewer}: {decisions}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    ok, message = AuditLog(args.audit).verify()
    print(("OK: " if ok else "TAMPERED: ") + message)
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="contractreview", description="Playbook-driven contract review on the Claude API.")
    sub = p.add_subparsers(dest="command", required=True)

    def review_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--offline", action="store_true", help="force the rule-based reviewer")
        sp.add_argument("--live", action="store_true", help="force the Claude API even without ANTHROPIC_API_KEY")
        sp.add_argument("--stream", action="store_true", help="stream Claude's analysis to the terminal")
        sp.add_argument("--model", default="claude-opus-5")
        sp.add_argument("--max-rounds", type=int, default=6)
        sp.add_argument("--no-fallback", action="store_true", help="disable server-side refusal fallbacks")
        sp.add_argument("--playbook", default=None)
        sp.add_argument("--analyst", default="analyst", help="who requested the review (the 'maker')")
        sp.add_argument("--out", default="outputs")
        sp.add_argument("--audit", default=DEFAULT_AUDIT)

    r = sub.add_parser("review", help="review one contract")
    r.add_argument("contract")
    review_flags(r)
    r.set_defaults(func=cmd_review)

    b = sub.add_parser("batch", help="review every .md contract in a directory")
    b.add_argument("directory")
    review_flags(b)
    b.set_defaults(func=cmd_batch)

    s = sub.add_parser("signoff", help="accept/reject each high-risk finding")
    s.add_argument("review_json")
    s.add_argument("--reviewer", required=True, help="the 'checker'; must differ from the analyst")
    s.add_argument("--decisions", help='JSON file {"F01": {"decision": "accept", "reason": ""}}')
    s.add_argument("--audit", default=DEFAULT_AUDIT)
    s.set_defaults(func=cmd_signoff)

    v = sub.add_parser("verify-audit", help="check the audit hash chain")
    v.add_argument("--audit", default=DEFAULT_AUDIT)
    v.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
