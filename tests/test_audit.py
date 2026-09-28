import json

import pytest

from contractreview.audit import AuditLog, SignoffError, record_review, sign_off
from contractreview.cli import main
from contractreview.offline import review_offline
from tests.conftest import CONTRACTS


@pytest.fixture
def result(saas, playbook):
    return review_offline(saas, playbook, requested_by="alice")


def test_maker_cannot_check(result, tmp_path):
    with pytest.raises(SignoffError, match="maker-checker"):
        sign_off(result, "Alice", lambda f: ("accept", ""), AuditLog(tmp_path / "a.jsonl"))


def test_reject_requires_reason(result, tmp_path):
    with pytest.raises(SignoffError, match="reason is required"):
        sign_off(result, "bob", lambda f: ("reject", " "), AuditLog(tmp_path / "a.jsonl"))


def test_incomplete_review_cannot_be_signed(result, tmp_path):
    result.status = "refused"
    with pytest.raises(SignoffError, match="status"):
        sign_off(result, "bob", lambda f: ("accept", ""), AuditLog(tmp_path / "a.jsonl"))


def test_signoff_covers_every_high_risk_finding(result, tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    record_review(log, result)
    decisions = sign_off(
        result, "bob", lambda f: ("reject", "commercially agreed") if f.rule_id == "PB-PAY-03" else ("accept", ""), log
    )
    high = {f.id for f in result.findings if f.risk == "high"}
    assert set(decisions) == high and len(high) == 5
    events = [e["event"] for e in log.entries()]
    assert events[0] == "review_completed" and events[-1] == "signoff_completed"
    assert log.verify() == (True, f"{len(events)} entries verified")


def _signed_log(result, tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    record_review(log, result)
    sign_off(result, "bob", lambda f: ("accept", ""), log)
    return log


def test_tamper_edit_detected(result, tmp_path):
    log = _signed_log(result, tmp_path)
    lines = log.path.read_text().splitlines()
    entry = json.loads(lines[2])
    entry["payload"]["decision"] = "reject"
    lines[2] = json.dumps(entry, sort_keys=True)
    log.path.write_text("\n".join(lines) + "\n")
    ok, msg = log.verify()
    assert not ok and msg.startswith("entry 3") and "modified" in msg


def test_tamper_delete_and_reorder_detected(result, tmp_path):
    log = _signed_log(result, tmp_path)
    lines = log.path.read_text().splitlines()
    log.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert log.verify()[0] is False
    log.path.write_text("\n".join([lines[1], lines[0], *lines[2:]]) + "\n")
    assert log.verify()[0] is False


def test_cli_signoff_with_decisions_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, audit = tmp_path / "out", tmp_path / "audit.jsonl"
    assert (
        main(
            ["review", str(CONTRACTS / "mutual_nda.md"), "--out", str(out), "--audit", str(audit), "--analyst", "alice"]
        )
        == 0
    )
    review_json = out / "mutual_nda.review.json"
    decisions = tmp_path / "d.json"
    decisions.write_text(json.dumps({"F01": {"decision": "accept"}}))
    base = ["signoff", str(review_json), "--decisions", str(decisions), "--audit", str(audit)]
    assert main([*base, "--reviewer", "alice"]) == 1
    assert main([*base, "--reviewer", "bob"]) == 0
    assert main(["verify-audit", "--audit", str(audit)]) == 0
