"""Live-path tests against a mocked Anthropic client (no network, no key)."""

import anthropic
import httpx2
import pytest

from contractreview.claude_reviewer import ClaudeReviewer, ReviewConfig, cost_usd
from contractreview.models import UsageReport
from tests.conftest import FakeClient, make_message

LIABILITY = "Customer's liability under this Agreement shall be unlimited."


def tool_use(name, args, id_="tu_1"):
    return {"type": "tool_use", "id": id_, "name": name, "input": args}


def submit(findings, id_="tu_submit"):
    return tool_use("submit_review", {"summary": "Several high-risk deviations.", "findings": findings}, id_)


def finding(rule_id="PB-LIA-02", quote=LIABILITY, clause="7. Limitation of Liability"):
    return {
        "rule_id": rule_id,
        "clause": clause,
        "risk": "high",
        "issue": "Uncapped customer liability.",
        "quote": quote,
        "recommended_redline": "Make the cap mutual.",
    }


def reviewer(script, playbook, **cfg):
    client = FakeClient(script)
    sleeps: list[float] = []
    r = ClaudeReviewer(playbook, client=client, config=ReviewConfig(**cfg), sleep=sleeps.append)
    return r, client.beta.messages, sleeps


def status_error(cls, code, headers=None):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(code, request=req, headers=headers or {}), body=None)


def test_tool_use_round_trip_and_cache_accounting(saas, playbook):
    script = [
        make_message(
            [tool_use("get_clause", {"section": "7"})],
            "tool_use",
            {
                "input_tokens": 900,
                "output_tokens": 80,
                "cache_creation_input_tokens": 2400,
                "cache_read_input_tokens": 0,
            },
        ),
        make_message(
            [submit([finding(), finding("PB-LIA-01", "invented text that is not in the contract")])],
            "tool_use",
            {
                "input_tokens": 300,
                "output_tokens": 400,
                "cache_creation_input_tokens": 600,
                "cache_read_input_tokens": 2400,
            },
        ),
    ]
    r, calls, _ = reviewer(script, playbook)
    result = r.review(saas)

    assert result.status == "complete" and result.reviewer == "claude-opus-5"
    assert [f.rule_id for f in result.findings] == ["PB-LIA-02"]
    assert result.findings[0].source == "claude"
    assert result.dropped[0].reason.startswith("quote not found")
    # Second request carried the tool_result for get_clause with section text.
    tool_result = calls.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and "shall be unlimited" in tool_result["content"]
    u = result.usage
    assert (u.rounds, u.input_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens) == (2, 1200, 2400, 3000)
    assert u.cost_usd == cost_usd("claude-opus-5", u) > 0


def test_request_shape_caching_citations_fallbacks(saas, playbook):
    r, calls, _ = reviewer([make_message([submit([])], "tool_use")], playbook)
    r.review(saas)
    p = calls.calls[0]
    assert p["model"] == "claude-opus-5" and p["thinking"] == {"type": "adaptive"}
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["fallbacks"] == "default" and p["betas"] == ["server-side-fallback-2026-07-01"]
    doc = p["messages"][0]["content"][0]
    assert doc["type"] == "document" and doc["citations"] == {"enabled": True}
    assert next(t for t in p["tools"] if t["name"] == "submit_review")["strict"] is True


def test_citations_parsed_and_verified(saas, playbook):
    start = saas.text.index(LIABILITY)
    good = {
        "type": "char_location",
        "cited_text": LIABILITY,
        "document_index": 0,
        "document_title": "saas_agreement",
        "start_char_index": start,
        "end_char_index": start + len(LIABILITY),
    }
    bogus = {**good, "start_char_index": 0, "end_char_index": len(LIABILITY)}  # offsets don't match text
    analysis = make_message(
        [{"type": "text", "text": "Customer liability is uncapped.", "citations": [good, bogus]}, submit([finding()])],
        "tool_use",
    )
    r, _, _ = reviewer([analysis], playbook)
    result = r.review(saas)
    assert result.findings[0].cited is True

    analysis_bogus = make_message(
        [{"type": "text", "text": "x", "citations": [bogus]}, submit([finding()])], "tool_use"
    )
    r2, _, _ = reviewer([analysis_bogus], playbook)
    assert r2.review(saas).findings[0].cited is False


def test_invalid_tool_input_is_reported_and_recovered(saas, playbook):
    bad = {"summary": "x", "findings": [{"rule_id": "PB-LIA-02", "risk": "catastrophic"}]}
    script = [
        make_message(
            [tool_use("submit_review", bad, "tu_bad"), tool_use("get_clause", {"section": "99"}, "tu_2")], "tool_use"
        ),
        make_message([submit([finding(rule_id="PB-NOPE-1")])], "tool_use"),
        make_message([submit([finding()])], "tool_use"),
    ]
    r, calls, _ = reviewer(script, playbook)
    result = r.review(saas)
    assert result.status == "complete" and len(result.findings) == 1
    first_results = calls.calls[1]["messages"][-1]["content"]
    assert all(tr["is_error"] for tr in first_results)
    assert "Invalid submit_review input" in first_results[0]["content"]
    assert "No section" in first_results[1]["content"]
    assert "Unknown rule ids" in calls.calls[2]["messages"][-1]["content"][0]["content"]


def test_tool_round_limit_is_enforced(saas, playbook):
    looping = [make_message([tool_use("get_clause", {"section": "1"}, f"tu_{i}")], "tool_use") for i in range(10)]
    r, calls, _ = reviewer(looping, playbook, max_rounds=3)
    result = r.review(saas)
    assert result.status == "incomplete" and "round limit (3)" in result.status_detail
    assert len(calls.calls) == 3 and result.findings == []


def test_end_turn_without_submit_gets_nudged(saas, playbook):
    script = [
        make_message([{"type": "text", "text": "Analysis done."}], "end_turn"),
        make_message([submit([finding()])], "tool_use"),
    ]
    r, calls, _ = reviewer(script, playbook)
    assert r.review(saas).status == "complete"
    assert "submit_review" in calls.calls[1]["messages"][-1]["content"]


def test_rate_limit_and_overload_are_retried(saas, playbook):
    script = [
        status_error(anthropic.RateLimitError, 429, {"retry-after": "7"}),
        status_error(anthropic.OverloadedError, 529),
        make_message([submit([finding()])], "tool_use"),
    ]
    r, _, sleeps = reviewer(script, playbook, base_delay=0.5)
    result = r.review(saas)
    assert result.status == "complete" and result.usage.retries == 2
    assert sleeps[0] >= 7  # honours retry-after
    assert 1.0 <= sleeps[1] < 1.5  # exponential: 0.5 * 2**1 + jitter


def test_retries_exhausted_raise(saas, playbook):
    script = [status_error(anthropic.RateLimitError, 429) for _ in range(3)]
    r, _, sleeps = reviewer(script, playbook, max_retries=2)
    with pytest.raises(anthropic.RateLimitError):
        r.review(saas)
    assert len(sleeps) == 2


def test_bad_request_not_retried(saas, playbook):
    r, calls, sleeps = reviewer([status_error(anthropic.BadRequestError, 400)], playbook)
    with pytest.raises(anthropic.BadRequestError):
        r.review(saas)
    assert sleeps == [] and len(calls.calls) == 1


def test_refusal_is_handled(saas, playbook):
    refusal = make_message([], "refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None})
    r, _, _ = reviewer([refusal], playbook)
    result = r.review(saas)
    assert result.status == "refused" and "cyber" in result.status_detail and result.findings == []


def test_max_tokens_stops_without_running_truncated_tools(saas, playbook):
    r, calls, _ = reviewer([make_message([submit([finding()])], "max_tokens")], playbook)
    result = r.review(saas)
    assert result.status == "incomplete" and result.findings == [] and len(calls.calls) == 1


def test_streaming_path(saas, playbook):
    chunks: list[str] = []
    client = FakeClient(
        [make_message([{"type": "text", "text": "Streaming analysis."}, submit([finding()])], "tool_use")]
    )
    r = ClaudeReviewer(
        playbook, client=client, config=ReviewConfig(stream=True, use_fallbacks=False), on_text=chunks.append
    )
    result = r.review(saas)
    assert chunks == ["Streaming analysis."] and result.status == "complete"
    params = client.beta.messages.calls[0]
    assert "fallbacks" not in params and all(t["eager_input_streaming"] for t in params["tools"])


def test_cost_math():
    u = UsageReport(
        model="claude-opus-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )
    assert cost_usd("claude-opus-5", u) == pytest.approx(5 + 25 + 6.25 + 0.5)
    assert u.cache_hit_ratio == pytest.approx(1 / 3)
