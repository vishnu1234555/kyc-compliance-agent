from langgraph.types import Command

from kyc_agent.graph import build_graph, run_config
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
    graph = build_graph()
    config = run_config()
    paused = graph.invoke(initial, config=config)
    # no header, no documents: R1 (no OVD) and R2 (no PAN, no Form 60) fire in the rules baseline,
    # so the graph pauses at human_review and reports the interrupt alongside the state
    assert set(paused) == set(KYCState.__annotations__) | {"__interrupt__"}
    assert paused["raw_document"] == "dummy"
    assert paused["decision"] == "re_kyc"
    assert paused["rules_verdict"]["rules"] == ["R1", "R2"]
    final = graph.invoke(Command(resume={"reviewer": "test", "action": "confirm"}), config=config)
    assert set(final) == set(KYCState.__annotations__)
    assert [entry["node"] for entry in final["audit_log"]] == ["extract", "validate", "retrieve_rules", "decide", "human_review"]
