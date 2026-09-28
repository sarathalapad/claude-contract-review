"""The review playbook: machine-checkable rules plus a standard clause library.

One JSON file drives both engines. The offline reviewer executes each rule's
`check`; the Claude reviewer receives the same rules rendered as markdown in a
cached system prompt, so the two can never silently disagree about policy.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from contractreview.models import RiskLevel

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Rule(BaseModel):
    id: str
    category: str
    title: str
    risk: RiskLevel
    applies_to: list[str]
    description: str
    check: dict[str, Any]
    standard_clause: str
    redline: str


class Playbook(BaseModel):
    name: str
    version: str
    rules: list[Rule]
    standard_clauses: dict[str, str] = {}

    @classmethod
    def load(cls, path: str | Path | None = None, clauses_path: str | Path | None = None) -> Playbook:
        pb = cls.model_validate(json.loads(Path(path or DATA_DIR / "playbook.json").read_text(encoding="utf-8")))
        raw = json.loads(Path(clauses_path or DATA_DIR / "standard_clauses.json").read_text(encoding="utf-8"))
        pb.standard_clauses = {k: v for k, v in raw.items() if not k.startswith("_")}
        return pb

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id.strip().upper()), None)

    def rules_for(self, contract_type: str) -> list[Rule]:
        return [r for r in self.rules if contract_type in r.applies_to]

    def to_markdown(self) -> str:
        """Render deterministically: this text is the cached prompt prefix, and
        any byte-level change between requests would invalidate the cache."""
        lines = [f"# {self.name} (version {self.version})", ""]
        for r in self.rules:
            lines += [
                f"## {r.id}: {r.title}",
                f"- Risk: {r.risk}",
                f"- Applies to: {', '.join(r.applies_to)}",
                f"- Rule: {r.description}",
                f"- Standard clause key: {r.standard_clause}",
                f"- Preferred redline: {r.redline}",
                "",
            ]
        return "\n".join(lines)

    def compare_to_standard(self, clause_key: str, text: str) -> dict[str, Any]:
        """Similarity to our standard wording, plus standard terms the contract lacks.

        A cheap lexical signal: it tells the reviewer *where* to look, while the
        judgement about materiality stays with Claude or the human reviewer.
        """
        standard = self.standard_clauses.get(clause_key)
        if standard is None:
            return {"error": f"unknown clause key '{clause_key}'", "known_keys": sorted(self.standard_clauses)}
        ratio = difflib.SequenceMatcher(None, standard.lower(), text.lower()).ratio()
        terms = set(re.findall(r"\(\d+\)|\b[a-z]{7,}\b", standard.lower()))
        missing = sorted(t for t in terms if t not in text.lower())
        return {
            "clause_key": clause_key,
            "standard_text": standard,
            "similarity": round(ratio, 2),
            "missing_terms": missing,
        }
