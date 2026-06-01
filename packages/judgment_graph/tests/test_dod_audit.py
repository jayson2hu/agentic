from judgment_graph.scripts.audit_dod import audit_items


def test_dod_audit_has_explicit_evidence_and_known_pending_items() -> None:
    items = audit_items()
    by_requirement = {item.requirement: item for item in items}

    assert by_requirement["L2 table ownership"].status == "PASS"
    assert by_requirement["No L3 dependency"].status == "PASS"
    assert by_requirement["Recommend and companion APIs"].status == "PASS"
    assert by_requirement["Runtime PostgreSQL migration"].status in {"PASS", "PENDING_ENV"}
    assert by_requirement["Redis-backed Arq integration"].status in {"PASS", "PENDING_ENV"}
