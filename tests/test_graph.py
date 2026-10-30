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
        "audit_log": [],
    }
    final = build_graph().invoke(initial)
    assert set(final) == set(KYCState.__annotations__)
    assert final["raw_document"] == "dummy"
    assert final["decision"] == ""
