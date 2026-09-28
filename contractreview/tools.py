"""Client-side tools Claude can call during a review, and their executors.

The investigative tools keep the model honest and cheap: instead of asking it
to recall every rule and clause from a long prompt, it can fetch exactly the
rule text, section, or standard wording it needs. `submit_review` is a strict
tool whose input *is* the structured output, validated again with pydantic.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from contractreview.contract import Contract
from contractreview.models import SubmittedReview
from contractreview.playbook import Playbook

SUBMIT_TOOL = "submit_review"

_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "rule_id": {"type": "string", "description": "Playbook rule id, e.g. PB-LIA-01."},
        "clause": {"type": "string", "description": "Section label, or '(missing)' for an absent clause."},
        "risk": {"type": "string", "enum": ["high", "medium", "low"]},
        "issue": {"type": "string", "description": "One sentence: what is wrong and why it matters."},
        "quote": {
            "type": "string",
            "description": "Exact verbatim text copied from the contract; empty string only for a missing clause.",
        },
        "recommended_redline": {"type": "string", "description": "Replacement or inserted contract wording."},
    },
    "required": ["rule_id", "clause", "risk", "issue", "quote", "recommended_redline"],
    "additionalProperties": False,
}

# Order and content are fixed: tools render first in the cached prefix, so any
# change here invalidates the prompt cache for every subsequent review.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_playbook_rule",
        "description": "Return the full text of one playbook rule (risk, scope, preferred redline). Use before citing a rule you are unsure about.",
        "input_schema": {
            "type": "object",
            "properties": {"rule_id": {"type": "string", "description": "e.g. PB-DAT-01"}},
            "required": ["rule_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_clause",
        "description": "Return the full text of a contract section by number ('7') or title fragment ('liability').",
        "input_schema": {
            "type": "object",
            "properties": {"section": {"type": "string"}},
            "required": ["section"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_to_standard_template",
        "description": "Compare a contract section with our standard clause wording. Returns similarity (0-1) and standard terms missing from the section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "clause_key": {
                    "type": "string",
                    "description": "Standard clause key from the playbook, e.g. limitation_of_liability.",
                },
                "section": {"type": "string", "description": "Contract section number or title fragment."},
            },
            "required": ["clause_key", "section"],
            "additionalProperties": False,
        },
    },
    {
        "name": SUBMIT_TOOL,
        "description": "Submit the final review exactly once, after investigating. Every quote must be copied verbatim from the contract.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Two or three sentence overall assessment."},
                "findings": {"type": "array", "items": _FINDING_SCHEMA},
            },
            "required": ["summary", "findings"],
            "additionalProperties": False,
        },
    },
]


class ToolError(Exception):
    """Raised for bad tool input; reported back to Claude as is_error=True."""


def execute_tool(name: str, args: dict[str, Any], contract: Contract, playbook: Playbook) -> str:
    """Run an investigative tool and return a JSON string for the tool_result."""
    if name == "lookup_playbook_rule":
        rule = playbook.get(str(args.get("rule_id", "")))
        if rule is None:
            raise ToolError(f"No rule {args.get('rule_id')!r}. Valid ids: {', '.join(r.id for r in playbook.rules)}")
        return rule.model_dump_json(exclude={"check"})
    if name in ("get_clause", "compare_to_standard_template"):
        sec = contract.get_clause(str(args.get("section", "")))
        if sec is None:
            raise ToolError(
                f"No section {args.get('section')!r}. Sections: {'; '.join(s.label for s in contract.sections)}"
            )
        if name == "get_clause":
            return json.dumps({"section": sec.label, "text": contract.section_text(sec)})
        result = playbook.compare_to_standard(str(args.get("clause_key", "")), contract.section_text(sec))
        if "error" in result:
            raise ToolError(json.dumps(result))
        return json.dumps({"section": sec.label, **result})
    raise ToolError(f"Unknown tool {name!r}")


def parse_submission(args: Any, playbook: Playbook) -> SubmittedReview:
    """Validate submit_review input. Strict tool use should guarantee the
    shape, but we re-validate so a malformed or eagerly-streamed input can
    never reach the report, and we check rule ids the schema cannot know."""
    try:
        review = SubmittedReview.model_validate(args)
    except ValidationError as exc:
        raise ToolError(f"Invalid submit_review input: {exc.error_count()} error(s): {exc.errors()[0]['msg']}") from exc
    unknown = sorted({f.rule_id for f in review.findings if playbook.get(f.rule_id) is None})
    if unknown:
        raise ToolError(f"Unknown rule ids {unknown}; use lookup_playbook_rule or the playbook ids only.")
    return review
