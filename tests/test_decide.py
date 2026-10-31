from datetime import date
from typing import Any

import pytest

from kyc_agent import nodes
from kyc_agent.decide import Verdict, build_messages, keep_retrieved_citations, llm_decide, rules_decide
from kyc_agent.graph import build_graph, run_config

AS_OF = date(2026, 10, 30)
RETRIEVED = [
    {"rule": "R1", "citation": "para 5(1)(xiv)", "text": "OVD means ..."},
    {"rule": "R1", "citation": "para 23(3)", "text": "proof of possession of Aadhaar or any OVD ..."},
    {"rule": "R3", "citation": "para 23 Explanation 1", "text": "redacts or blacks out ..."},
]


def _state(fields: dict, errors: list[str] | None = None, documents: list[dict] | None = None, retrieved: list[dict] | None = None) -> dict:
    return {"raw_document": "", "extracted_fields": fields, "documents": documents or [], "validation_errors": errors or [],
            "findings": [], "retrieved_rules": retrieved if retrieved is not None else RETRIEVED, "rbi_citations": [],
            "decision": "", "reason": "", "rules_verdict": {}, "audit_log": []}


def test_rules_decide_follows_the_policy_table() -> None:
    fields = {"name": "SURESH BABU MAHAPATRA", "pan": "STHPM4410G", "form60": False, "doc_type": "pension_order",
              "aadhaar": "XXXX-XXXX-1122", "risk_category": "low", "last_kyc_date": "30-03-2021"}
    out = rules_decide(_state(fields, ["Aadhaar must be masked as XXXX-XXXX-1234"]), as_of=AS_OF)
    assert out.decision == "re_kyc"
    assert out.citations == ["para 5(1)(xiv)", "para 23(3)", "para 23 Explanation 1"]
    assert out.rules == ["R1", "R3"]


def test_rules_decide_escalate_wins_and_r6_is_skipped() -> None:
    fields = {"name": "DEMO USER", "pan": "DDDPU0000D", "form60": False, "doc_type": "driving_licence",
              "risk_category": "high", "last_kyc_date": "09-09-2022"}
    out = rules_decide(_state(fields), as_of=AS_OF)
    assert out.decision == "re_kyc" and out.citations == ["para 42(1)"]  # R6 needs judgment: baseline does not fire it
    documents = [{"kind": "passport", "name": "ANJALI MEHRA"}, {"kind": "pan_card", "name": "ANJALI MALHOTRA"}]
    clean = {"name": "ANJALI MEHRA", "pan": "VXKPM3321S", "form60": False, "doc_type": "passport",
             "risk_category": "high", "last_kyc_date": "01-04-2026"}
    out = rules_decide(_state(clean, documents=documents), as_of=AS_OF)
    assert out.decision == "escalate" and out.citations == ["para 17(2)"] and out.rules == ["R7"]
    approve = rules_decide(_state({"pan": "AKLPR4821Q", "form60": False, "doc_type": "passport", "risk_category": "medium",
                                   "last_kyc_date": "14-02-2022"}), as_of=AS_OF)
    assert approve.decision == "approve" and approve.citations == [] and approve.rules == []


def test_prompt_carries_fields_errors_and_retrieved_rules_only() -> None:
    state = _state({"aadhaar": "XXXX-XXXX-1122", "name": "A B"}, ["PIN code invalid"])
    state["raw_document"] = "SECRET RAW TEXT 9999 7788 1122"
    system, human = build_messages(state, as_of=AS_OF)
    assert system[0] == "system" and "escalate > re_kyc > approve" in system[1]
    assert "As-of date: 30-10-2026" in human[1] and "PIN code invalid" in human[1] and "[para 23(3)]" in human[1]
    assert "SECRET" not in human[1] and "9999 7788 1122" not in human[1]


def test_citations_outside_the_retrieved_set_are_dropped() -> None:
    kept, dropped = keep_retrieved_citations(["para 23(3)", "para 99", "para 23(3)", "para 42(1)"], _state({}))
    assert kept == ["para 23(3)"] and dropped == ["para 99", "para 42(1)"]
    assert keep_retrieved_citations(["para 23(3)"], _state({}), decision="approve") == ([], ["para 23(3)"])


class FakeChat:
    """Stands in for ChatGoogleGenerativeAI: returns the verdict it was given, remembers the prompt."""

    def __init__(self, verdict: Verdict | Exception) -> None:
        self.verdict = verdict
        self.prompts: list[Any] = []

    def with_structured_output(self, schema: type) -> "FakeChat":
        assert schema is Verdict
        return self

    def invoke(self, messages: Any) -> Verdict:
        self.prompts.append(messages)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def test_llm_decide_uses_structured_output_and_filters_citations() -> None:
    chat = FakeChat(Verdict(decision="re_kyc", citations=["para 23 Explanation 1", "para 70"], reason="R3:  Aadhaar  visible"))
    out = llm_decide(_state({"aadhaar": "XXXX-XXXX-1122"}, ["Aadhaar must be masked as XXXX-XXXX-1234"]), chat, as_of=AS_OF)
    assert out.decision == "re_kyc" and out.citations == ["para 23 Explanation 1"] and out.dropped_citations == ["para 70"]
    assert out.reason == "R3: Aadhaar visible" and len(chat.prompts) == 1


def test_decide_node_llm_mode_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state({"pan": "AKLPR4821Q", "form60": False, "doc_type": "utility_bill", "risk_category": "medium", "last_kyc_date": "14-02-2022"})
    monkeypatch.setenv("DECIDE_MODE", "llm")
    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(Verdict(decision="re_kyc", citations=["para 5(1)(xiv)", "para 23(3)"], reason="R1")))
    out = nodes.decide(state)
    assert out["decision"] == "re_kyc" and out["rbi_citations"] == ["para 5(1)(xiv)", "para 23(3)"] and out["reason"] == "R1"
    assert out["rules_verdict"]["decision"] == "re_kyc" and out["rules_verdict"]["rules"] == ["R1"]
    entry = out["audit_log"][-1]
    assert entry["node"] == "decide" and entry["mode"] == "llm" and entry["rules_baseline"] == "re_kyc" and "llm_error" not in entry

    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(ValueError("key missing")))
    fallback = nodes.decide(state)
    assert fallback["decision"] == "re_kyc" and fallback["reason"].startswith("LLM unavailable")
    assert fallback["audit_log"][-1]["llm_error"].startswith("ValueError")


def test_decide_node_rules_mode_never_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_MODE", "rules")
    monkeypatch.setattr(nodes, "get_chat_model", lambda: (_ for _ in ()).throw(AssertionError("LLM called")))
    out = nodes.decide(_state({"pan": "AKLPR4821Q", "form60": False, "doc_type": "passport", "risk_category": "medium", "last_kyc_date": "14-02-2022"}))
    assert out["decision"] == "approve" and out["audit_log"][-1]["mode"] == "rules"


def test_approve_skips_human_review_and_others_do_not(monkeypatch: pytest.MonkeyPatch) -> None:
    visited: list[str] = []

    def spy(state: dict) -> dict:
        visited.append("human_review")
        return state

    monkeypatch.setattr(nodes, "human_review", spy)
    monkeypatch.setenv("DECIDE_MODE", "rules")
    clean = "KYC RECORD\nCustomer ID: X\nRisk category: Medium\nLast KYC date: 14-02-2022\n\n[Document 1: Passport]\nName: A B\nDate of birth: 01-01-1990\nPIN: 560001\n\n[Document 2: PAN card]\nPermanent Account Number: ABCPE1234F\n"
    final = build_graph().invoke(_state({}) | {"raw_document": clean}, config=run_config())
    assert final["decision"] == "approve" and visited == []
    bill = clean.replace("[Document 1: Passport]", "[Document 1: Electricity bill]")
    final = build_graph().invoke(_state({}) | {"raw_document": bill}, config=run_config())
    assert final["decision"] == "re_kyc" and visited == ["human_review"]


def test_decide_node_agent_mode_logs_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.messages import AIMessage

    class OneShotAgentChat:
        def bind_tools(self, tools: Any, tool_choice: str = "any") -> "OneShotAgentChat":
            return self

        def invoke(self, messages: Any) -> AIMessage:
            if len(messages) == 2:  # first turn: the required checks
                return AIMessage(content="", tool_calls=[
                    {"name": "check_kyc_due", "args": {"risk_category": "high", "last_kyc_date": "15-06-2023"}, "id": "c1"},
                    {"name": "compare_across_documents", "args": {"field": "name"}, "id": "c2"},
                    {"name": "compare_across_documents", "args": {"field": "dob"}, "id": "c3"},
                ])
            return AIMessage(content="", tool_calls=[{"name": "submit_verdict", "args": {"decision": "re_kyc", "citations": ["para 23(3)"], "reason": "R4"}, "id": "c4"}])

    monkeypatch.setenv("DECIDE_MODE", "agent")
    monkeypatch.setattr(nodes, "get_chat_model", lambda: OneShotAgentChat())
    state = _state({"pan": "AKLPR4821Q", "form60": False, "doc_type": "passport", "risk_category": "high", "last_kyc_date": "15-06-2023"})
    out = nodes.decide(state)
    entry = out["audit_log"][-1]
    assert out["decision"] == "re_kyc" and entry["mode"] == "agent" and entry["llm_calls"] == 2
    assert entry["tool_calls"][0]["tool"] == "check_kyc_due" and '"overdue": true' in entry["tool_calls"][0]["result"]
    assert out["rbi_citations"] == ["para 23(3)"] and out["rules_verdict"]["rules"] == ["R4"]
