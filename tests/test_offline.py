import json

import pytest

from contractreview.cli import main
from contractreview.contract import Contract
from contractreview.offline import review_offline
from tests.conftest import CONTRACTS

EXPECTED = {
    "saas_agreement": {
        "PB-LIA-01",
        "PB-LIA-02",
        "PB-TRM-01",
        "PB-REN-01",
        "PB-PAY-01",
        "PB-PAY-02",
        "PB-PAY-03",
        "PB-DAT-01",
        "PB-DAT-02",
        "PB-GOV-01",
    },
    "data_processing_agreement": {"PB-DAT-01", "PB-DPA-01", "PB-DPA-02"},
    "mutual_nda": {"PB-NDA-01", "PB-NDA-02"},
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_offline_review_per_contract(name, playbook):
    contract = Contract.load(CONTRACTS / f"{name}.md")
    result = review_offline(contract, playbook)
    assert result.status == "complete"
    assert {f.rule_id for f in result.findings} == EXPECTED[name]
    assert not result.dropped
    for f in result.findings:
        # Every non-missing finding is a verbatim, correctly located span.
        if f.quote:
            assert contract.text[f.start_char : f.end_char] == f.quote
    assert [f.id for f in result.findings] == [f"F{i:02d}" for i in range(1, len(result.findings) + 1)]


def test_offline_is_deterministic(playbook, saas):
    a = review_offline(saas, playbook).findings
    b = review_offline(saas, playbook).findings
    assert [f.model_dump() for f in a] == [f.model_dump() for f in b]


def test_cli_batch_offline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out, audit = tmp_path / "out", tmp_path / "audit.jsonl"
    code = main(["batch", str(CONTRACTS), "--out", str(out), "--audit", str(audit)])
    assert code == 0
    assert (out / "summary.md").exists()
    data = json.loads((out / "saas_agreement.review.json").read_text(encoding="utf-8"))
    assert data["reviewer"] == "offline-rules" and len(data["findings"]) == 10
    md = (out / "saas_agreement.review.md").read_text(encoding="utf-8")
    assert "Not legal advice" in md and "PB-LIA-01" in md
    assert main(["verify-audit", "--audit", str(audit)]) == 0
