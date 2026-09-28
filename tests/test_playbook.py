from contractreview.contract import Contract
from contractreview.offline import CHECKS


def _run(playbook, rule_id, text):
    rule = playbook.get(rule_id)
    return CHECKS[rule.check["type"]](rule, Contract.from_text("t", text))


def test_playbook_loads_and_scopes_rules(playbook):
    assert playbook.get("pb-lia-01").id == "PB-LIA-01"
    assert {r.id for r in playbook.rules_for("nda")} >= {"PB-NDA-01", "PB-NDA-02", "PB-GOV-01"}
    assert "PB-DPA-01" not in {r.id for r in playbook.rules_for("saas")}


def test_markdown_render_is_deterministic(playbook):
    # Byte-stable output is what makes the system prompt cacheable.
    assert playbook.to_markdown() == playbook.to_markdown()
    assert "PB-DAT-01" in playbook.to_markdown()


def test_liability_cap_minimum(playbook):
    low = "## 7. Liability\n\nLiability shall not exceed the fees paid in the six (6) months before the claim."
    ok = "## 7. Liability\n\nLiability shall not exceed the fees paid in the twelve (12) months before the claim."
    assert len(_run(playbook, "PB-LIA-01", low)) == 1
    assert _run(playbook, "PB-LIA-01", ok) == []


def test_breach_notice_converts_days_to_hours(playbook):
    assert _run(playbook, "PB-DPA-01", "Processor shall report a breach within four (4) days.")
    assert not _run(playbook, "PB-DPA-01", "Processor shall report a breach within forty-eight (48) hours.")


def test_payment_and_late_fee(playbook):
    assert _run(playbook, "PB-PAY-01", "Customer shall pay each invoice within thirty (30) days.")
    assert not _run(playbook, "PB-PAY-01", "Customer shall pay each invoice within sixty (60) days.")
    assert _run(playbook, "PB-PAY-02", "Late amounts bear interest at 1.5% per month.")
    assert not _run(playbook, "PB-PAY-02", "Late amounts bear interest at 0.5% per month.")


def test_required_clause_missing_and_present(playbook):
    missing = _run(playbook, "PB-TRM-01", "Either party may terminate for material breach.")
    assert missing and missing[0].clause == "(missing)" and missing[0].quote == ""
    assert not _run(playbook, "PB-TRM-01", "Customer may terminate for convenience on 90 days notice.")


def test_data_residency_allowlist(playbook):
    assert _run(playbook, "PB-DAT-01", "Data is hosted in the United Kingdom and India.")
    assert not _run(playbook, "PB-DAT-01", "Data is hosted in the United Kingdom and the European Union.")


def test_forbidden_and_governing_law(playbook):
    assert _run(playbook, "PB-NDA-02", "Either party may use Residuals freely.")
    assert _run(playbook, "PB-GOV-01", "This Agreement is governed by the laws of New York.")
    assert not _run(playbook, "PB-GOV-01", "This Agreement is governed by the laws of Ireland.")


def test_compare_to_standard(playbook):
    result = playbook.compare_to_standard("payment_terms", "Customer shall pay within 15 days.")
    assert 0 <= result["similarity"] < 0.6
    assert "(45)" in result["missing_terms"]
    assert "error" in playbook.compare_to_standard("nope", "x")
