from contractreview.grounding import ground_findings
from contractreview.models import Finding


def _f(rule_id="PB-LIA-02", quote="", clause="7. Limitation of Liability", risk="high"):
    return Finding(
        rule_id=rule_id, clause=clause, risk=risk, issue="x", quote=quote, recommended_redline="y", source="claude"
    )


def test_fabricated_quote_is_dropped(saas, playbook):
    kept, dropped = ground_findings([_f(quote="Customer's liability shall be capped at zero.")], saas, playbook)
    assert kept == [] and dropped[0].reason.startswith("quote not found")


def test_whitespace_tolerant_and_relabels_clause(saas, playbook):
    quote = "Customer's   liability under this\nAgreement shall be unlimited."
    kept, _ = ground_findings([_f(quote=quote, clause="12. Made-up section")], saas, playbook)
    assert kept[0].clause == "7. Limitation of Liability"
    assert saas.text[kept[0].start_char : kept[0].end_char] == kept[0].quote


def test_curly_quotes_are_normalised(saas, playbook):
    kept, _ = ground_findings(
        [_f(quote="Customer" + chr(0x2019) + "s liability under this Agreement shall be unlimited.")], saas, playbook
    )
    assert len(kept) == 1


def test_unknown_rule_dropped(saas, playbook):
    kept, dropped = ground_findings([_f(rule_id="PB-XXX-99", quote="shall be unlimited")], saas, playbook)
    assert not kept and "unknown playbook rule" in dropped[0].reason


def test_missing_clause_claims_are_verified(saas, playbook):
    # Termination for convenience really is absent; the liability clause is not.
    kept, dropped = ground_findings([_f("PB-TRM-01", risk="low"), _f("PB-REQ-01")], saas, playbook)
    assert [f.rule_id for f in kept] == ["PB-TRM-01"]
    assert kept[0].risk == "medium"  # risk comes from the playbook, not the model
    assert "present in the contract" in dropped[0].reason


def test_empty_quote_for_non_required_rule_dropped(saas, playbook):
    _, dropped = ground_findings([_f("PB-LIA-02")], saas, playbook)
    assert "no quote" in dropped[0].reason


def test_citation_overlap_marks_cited(saas, playbook):
    start = saas.text.index("Customer's liability")
    kept, _ = ground_findings(
        [_f(quote="Customer's liability under this Agreement shall be unlimited.")],
        saas,
        playbook,
        [(start + 5, start + 20)],
    )
    assert kept[0].cited is True


def test_duplicates_collapse(saas, playbook):
    q = "Customer's liability under this Agreement shall be unlimited."
    kept, _ = ground_findings([_f(quote=q), _f(quote=q)], saas, playbook)
    assert len(kept) == 1
