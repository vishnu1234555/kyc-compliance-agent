import sqlite3

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from evals.run_evals import fresh_state, run_graph
from kyc_agent.graph import build_graph, run_config

CLEAN = ("KYC RECORD\nCustomer ID: NB-1\nRisk category: Medium\nLast KYC date: 14-02-2022\n\n[Document 1: Passport]\nName: A B\n"
         "Date of birth: 01-01-1990\nPIN: 560001\n\n[Document 2: PAN card]\nPermanent Account Number: ABCPE1234F\n")
BILL = CLEAN.replace("[Document 1: Passport]", "[Document 1: Electricity bill]")


def test_graph_pauses_at_human_review_and_resumes_with_an_override() -> None:
    graph = build_graph()
    config = run_config("file-1")
    paused = graph.invoke(fresh_state(BILL), config=config)
    assert paused["decision"] == "re_kyc"
    payload = paused["__interrupt__"][0].value
    assert payload["decision"] == "re_kyc" and payload["customer_id"] == "NB-1" and payload["citations"] == ["para 5(1)(xiv)", "para 23(3)"]
    assert graph.get_state(config).next == ("human_review",)

    final = graph.invoke(Command(resume={"reviewer": "vishnu", "action": "override", "decision": "escalate", "note": "bill looks forged"}), config=config)
    assert "__interrupt__" not in final
    assert final["decision"] == "escalate" and final["reason"].endswith("Reviewer: bill looks forged")
    assert final["audit_log"][-1] == {"node": "human_review", "reviewer": "vishnu", "action": "override",
                                      "decision_before": "re_kyc", "decision": "escalate", "auto": False}
    assert graph.get_state(config).next == ()


def test_approve_never_pauses() -> None:
    final = build_graph().invoke(fresh_state(CLEAN), config=run_config("file-2"))
    assert final["decision"] == "approve" and "__interrupt__" not in final
    assert [entry["node"] for entry in final["audit_log"]] == ["extract", "validate", "retrieve_rules", "decide"]


def test_eval_runner_auto_resumes_and_records_it() -> None:
    graph = build_graph()
    _, final = run_graph(graph, BILL)
    assert final["decision"] == "re_kyc"
    assert final["audit_log"][-1]["node"] == "human_review" and final["audit_log"][-1]["auto"] is True
    assert final["audit_log"][-1]["reviewer"] == "eval-auto"
    _, clean = run_graph(graph, CLEAN)
    assert clean["audit_log"][-1]["node"] == "decide"


def test_sqlite_checkpointer_survives_a_new_graph_object(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "checkpoints.sqlite"
    saver = SqliteSaver(sqlite3.connect(str(db), check_same_thread=False))
    config = run_config("file-3")
    build_graph(saver).invoke(fresh_state(BILL), config=config)
    # a second process, later: same database, new graph object, resume
    final = build_graph(saver).invoke(Command(resume={"reviewer": "later", "action": "confirm"}), config=config)
    assert final["decision"] == "re_kyc" and final["audit_log"][-1]["reviewer"] == "later"
    monkeypatch.setenv("CHECKPOINT_DB", str(db))
    from kyc_agent.graph import default_checkpointer

    assert isinstance(default_checkpointer(), SqliteSaver)
