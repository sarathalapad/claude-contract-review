"""Contract loading, section lookup and quote grounding.

Grounding lives here, in plain code, because it is the safety net for both
reviewers: a finding is only as trustworthy as our ability to point at the
exact characters in the contract that justify it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^#{2,3}\s+(?:(\d+)\.\s+)?(.+?)\s*$", re.MULTILINE)
_TYPE_LINE = re.compile(r"^Contract type:\s*(\w+)", re.MULTILINE | re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.;])\s+(?=[A-Z(\"])")
_PUNCT = str.maketrans({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"})


@dataclass(frozen=True)
class Section:
    number: str
    title: str
    start: int
    end: int

    @property
    def label(self) -> str:
        return f"{self.number}. {self.title}" if self.number else self.title


@dataclass
class Contract:
    name: str
    text: str
    contract_type: str
    sections: list[Section] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Contract:
        p = Path(path)
        return cls.from_text(p.stem, p.read_text(encoding="utf-8"))

    @classmethod
    def from_text(cls, name: str, text: str) -> Contract:
        # Normalise line endings so character offsets match what Claude's
        # citations report for the same plain-text document.
        text = text.replace("\r\n", "\n")
        m = _TYPE_LINE.search(text)
        heads = list(_HEADING.finditer(text))
        sections = [
            Section(h.group(1) or "", h.group(2), h.start(), heads[i + 1].start() if i + 1 < len(heads) else len(text))
            for i, h in enumerate(heads)
        ]
        return cls(name, text, m.group(1).lower() if m else "unknown", sections)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def section_at(self, offset: int) -> Section | None:
        for s in self.sections:
            if s.start <= offset < s.end:
                return s
        return None

    def get_clause(self, query: str) -> Section | None:
        """Find a section by number ("7") or case-insensitive title fragment."""
        q = query.strip().rstrip(".").lower()
        for s in self.sections:
            if s.number == q or s.label.lower() == q:
                return s
        return next((s for s in self.sections if q and q in s.title.lower()), None)

    def section_text(self, section: Section) -> str:
        return self.text[section.start : section.end].strip()

    def sentences(self) -> list[tuple[int, int, str]]:
        """Body sentences with character spans; headings and banners are skipped."""
        out: list[tuple[int, int, str]] = []
        for line in re.finditer(r"[^\n]+", self.text):
            if line.group().lstrip().startswith(("#", ">")):
                continue
            pos = line.start()
            for part in _SENTENCE_BREAK.split(line.group()):
                start = self.text.index(part, pos)
                out.append((start, start + len(part), part))
                pos = start + len(part)
        return out

    def find_quote(self, quote: str) -> tuple[int, int] | None:
        """Locate a quote tolerant only of whitespace and typographic punctuation.

        We deliberately do not fuzzy-match: a paraphrase is not evidence, and
        the audit trail must show text that really is in the contract.
        """
        if not quote.strip():
            return None
        norm, index = _normalise_with_map(self.text)
        target, _ = _normalise_with_map(quote)
        pos = norm.find(target.strip())
        if pos < 0:
            return None
        end = pos + len(target.strip()) - 1
        return index[pos], index[end] + 1


def _normalise_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs and unify quotes/dashes, keeping an index map
    from each normalised character back to its original offset."""
    chars: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(text.translate(_PUNCT)):
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        chars.append(ch)
        index.append(i)
    return "".join(chars), index
