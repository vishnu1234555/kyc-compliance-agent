from kyc_agent.graph import build_graph
from kyc_agent.state import KYCState


def test_graph_runs_end_to_end() -> None:
    initial: KYCState = {
        "raw_document": "dummy",
        "extracted_fields": {},
        "documents": [],
        "validation_errors": [],
        "findings": [],
        "retrieved_rules": [],
        "rbi_citations": [],
        "decision": "",
        "reason": "",
        "rules_verdict": {},
        "audit_log": [],
    }
    final = build_graph().invoke(initial)
    assert set(final) == set(KYCState.__annotations__)
    assert final["raw_document"] == "dummy"
    # no header, no documents: R1 (no OVD) and R2 (no PAN, no Form 60) fire in the rules baseline
    assert final["decision"] == "re_kyc"
    assert final["rules_verdict"]["rules"] == ["R1", "R2"]
    assert [entry["node"] for entry in final["audit_log"]] == ["extract", "validate", "retrieve_rules", "decide"]
