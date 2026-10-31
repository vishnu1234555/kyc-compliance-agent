import json
from datetime import date
from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from kyc_agent.agent import MAX_TOOL_CALLS, agent_decide, make_tools, missing_required_calls
from kyc_agent.rules_index import FakeIndex

AS_OF = date(2026, 10, 30)
INDEX = FakeIndex([
    Document(page_content="(1) The NBFC shall carry out periodic updation at least once in every two years for high-risk customers", metadata={"citation": "para 42(1)"}),
    Document(page_content="(2) open no account where it is unable to apply appropriate CDD measures, non-cooperation of the customer or unreliability of the documents", metadata={"citation": "para 17(2)"}),
])


def _state() -> dict[str, Any]:
    return {
        "raw_document": "",
        "extracted_fields": {"name": "ANJALI MEHRA", "pan": "VXKPM3321S", "form60": False, "doc_type": "passport",
                             "risk_category": "high", "last_kyc_date": "01-04-2023"},
        "documents": [{"kind": "passport", "name": "ANJALI MEHRA"}, {"kind": "pan_card", "name": "ANJALI MALHOTRA"}],
        "validation_errors": [], "findings": [], "retrieved_rules": [{"rule": "R4", "citation": "para 42(1)", "text": "..."}],
        "rbi_citations": [], "decision": "", "reason": "", "rules_verdict": {}, "audit_log": [],
    }


class ScriptedToolChat:
    """A chat model whose replies are scripted tool calls; records the tool_choice it was bound with."""

    def __init__(self, script: list[list[dict[str, Any]]]) -> None:
        self.script = list(script)
        self.choices: list[str] = []
        self.seen: list[Any] = []

    def bind_tools(self, tools: list[Any], tool_choice: str = "any") -> "ScriptedToolChat":
        self.choices.append(tool_choice)
        self.tool_names = [t.name for t in tools]
        return self

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.seen.append(list(messages))
        calls = self.script.pop(0) if self.script else []
        if calls and calls[0].get("name") == "submit_verdict" and self.choices[-1] == "submit_verdict":
            pass
        return AIMessage(content="" if calls else "thinking out loud", tool_calls=[{**c, "id": f"call-{i}"} for i, c in enumerate(calls)])


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def test_tools_answer_from_the_state_and_the_index() -> None:
    retrieved: list[dict[str, Any]] = []
    search, due, compare, submit = make_tools(_state(), INDEX, 3, AS_OF, retrieved)
    assert json.loads(due.invoke({"risk_category": "high", "last_kyc_date": "01-04-2023"}))["due_date"] == "01-04-2025"
    comparison = json.loads(compare.invoke({"field": "name"}))
    assert comparison["mismatch"] is True and comparison["distinct"] == ["ANJALI MALHOTRA", "ANJALI MEHRA"]
    assert search.invoke({"query": "non-cooperation of the customer CDD"}).startswith("[para 17(2)]")
    assert retrieved[0]["citation"] == "para 17(2)"  # what the model searched for is now citable
    assert submit.invoke({"decision": "approve", "citations": [], "reason": "x"}) == "recorded"


def test_agent_loop_runs_tools_then_submits_and_filters_citations() -> None:
    chat = ScriptedToolChat([
        [_call("check_kyc_due", risk_category="high", last_kyc_date="01-04-2023"), _call("compare_across_documents", field="name")],
        [_call("compare_across_documents", field="dob"), _call("search_rbi_rules", query="unable to apply CDD measures non-cooperation documents")],
        [_call("submit_verdict", decision="escalate", citations=["para 42(1)", "para 17(2)", "para 99"], reason="R4 and R7")],
    ])
    state = _state()
    run = agent_decide(state, chat, INDEX, as_of=AS_OF)
    assert run.decision.decision == "escalate"
    assert run.decision.citations == ["para 42(1)", "para 17(2)"] and run.decision.dropped_citations == ["para 99"]
    assert [record.tool for record in run.tool_calls] == ["check_kyc_due", "compare_across_documents", "compare_across_documents", "search_rbi_rules"]
    assert '"overdue": true' in run.tool_calls[0].result and '"mismatch": true' in run.tool_calls[1].result
    assert run.llm_calls == 3 and chat.choices == ["any", "any", "any"] and run.reminded is False
    assert [chunk["citation"] for chunk in run.retrieved] == ["para 42(1)", "para 17(2)"]  # the search added 17(2)
    assert [chunk["citation"] for chunk in state["retrieved_rules"]] == ["para 42(1)"]  # the input state is not touched
    assert "You have at most 5 investigation calls" in chat.seen[0][0].content


def test_agent_is_forced_to_submit_after_the_budget() -> None:
    searches = [[_call("search_rbi_rules", query=f"query {i}")] for i in range(MAX_TOOL_CALLS + 1)]
    chat = ScriptedToolChat(searches + [[_call("submit_verdict", decision="re_kyc", citations=[], reason="budget")]])
    run = agent_decide(_state(), chat, INDEX, as_of=AS_OF)
    assert run.decision.decision == "re_kyc"
    assert run.budget_exhausted is True
    assert len([r for r in run.tool_calls if not r.result.startswith("Tool budget")]) == MAX_TOOL_CALLS
    assert chat.choices[-1] == "submit_verdict"


REQUIRED = [_call("check_kyc_due", risk_category="high", last_kyc_date="01-04-2023"),
            _call("compare_across_documents", field="name"), _call("compare_across_documents", field="dob")]


def test_agent_nudges_when_the_model_answers_without_a_tool() -> None:
    chat = ScriptedToolChat([[], REQUIRED, [_call("submit_verdict", decision="approve", citations=["para 42(1)"], reason="nothing fires")]])
    run = agent_decide(_state(), chat, INDEX, as_of=AS_OF)
    assert run.decision.decision == "approve" and run.llm_calls == 3
    assert run.decision.citations == [] and run.decision.dropped_citations == ["para 42(1)"]  # an approve cites nothing
    assert chat.seen[1][-1].content == "Call submit_verdict with your decision now."


def test_agent_is_sent_back_once_when_it_submits_too_early() -> None:
    chat = ScriptedToolChat([
        [_call("submit_verdict", decision="approve", citations=[], reason="looks fine")],
        REQUIRED,
        [_call("submit_verdict", decision="escalate", citations=["para 17(2)"], reason="name mismatch")],
    ])
    run = agent_decide(_state(), chat, INDEX, as_of=AS_OF)
    assert run.reminded is True and run.decision.decision == "escalate"
    assert run.tool_calls[0].tool == "submit_verdict" and run.tool_calls[0].result.startswith("Not recorded. Before submitting, call: check_kyc_due")
    assert missing_required_calls(run.tool_calls) == []


def test_agent_rejects_an_unknown_decision() -> None:
    chat = ScriptedToolChat([REQUIRED, [_call("submit_verdict", decision="maybe", citations=[], reason="?")]])
    with pytest.raises(ValueError):
        agent_decide(_state(), chat, INDEX, as_of=AS_OF)
