"""The agentic decide: Gemini chooses which tools to call, in what order, and when to stop.

Tools (plain functions the model can call, each logged in the audit trail):
- search_rbi_rules(query): the retrieve_rules index, so the model can look up a paragraph itself.
- check_kyc_due(risk_category, last_kyc_date): exact date maths for para 42(1). LLMs get date
  arithmetic wrong, so the answer comes from code.
- compare_across_documents(field): that field's value in every document of the file, so a
  name or date-of-birth mismatch (R7) can be seen.
- submit_verdict(decision, citations, reason): how the model ends the loop. Its arguments are
  the structured output.

At most MAX_TOOL_CALLS investigation calls per case; after that the model is told to submit.
Citations are still filtered in code to paragraphs that were actually retrieved, either by
retrieve_rules or by the model's own searches.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from kyc_agent.backoff import with_backoff
from kyc_agent.config import get_settings
from kyc_agent.decide import SYSTEM_PROMPT, Decision, Verdict, build_messages, keep_retrieved_citations
from kyc_agent.rules import compare_across_documents as compare_documents
from kyc_agent.rules import kyc_due
from kyc_agent.rules_index import RulesIndex

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5
INVESTIGATION_TOOLS = ("search_rbi_rules", "check_kyc_due", "compare_across_documents")
# A verdict is not accepted until these have been called once (the loop reminds the model once).
REQUIRED_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("check_kyc_due", {}),
    ("compare_across_documents", {"field": "name"}),
    ("compare_across_documents", {"field": "dob"}),
)

AGENT_INSTRUCTIONS = """
You have tools. Use them before deciding:
- check_kyc_due for the periodic-updation rule: never work out the dates yourself.
- compare_across_documents("name") and compare_across_documents("dob") to see whether the documents in the file disagree.
- search_rbi_rules when you need a paragraph that is not among the retrieved rules; you may only cite paragraphs returned by a tool or listed under RETRIEVED RULES.
Finish by calling submit_verdict exactly once; it is only recorded after check_kyc_due and both compare_across_documents calls have been made. You have at most {budget} investigation calls."""


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    result: str

    def as_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "result": self.result[:400]}


@dataclass
class AgentRun:
    decision: Decision
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    llm_calls: int = 0
    budget_exhausted: bool = False
    reminded: bool = False  # the model tried to submit before the required checks and was sent back once
    retrieved: list[dict[str, Any]] = field(default_factory=list)  # retrieve_rules' chunks plus the agent's own searches


def missing_required_calls(records: list[ToolCallRecord]) -> list[str]:
    """The required checks not yet made, as the model should call them."""
    missing: list[str] = []
    for name, args in REQUIRED_CALLS:
        done = any(record.tool == name and all(record.args.get(k) == v for k, v in args.items()) for record in records)
        if not done:
            shown = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "risk_category, last_kyc_date"
            missing.append(f"{name}({shown})")
    return missing


def make_tools(state: dict[str, Any], index: RulesIndex, k: int, as_of: date | None, retrieved: list[dict[str, Any]]) -> list[Any]:
    """Tools bound to this file: compare_across_documents reads the state's documents, search adds to `retrieved`."""

    @tool
    def search_rbi_rules(query: str) -> str:
        """Search the RBI (NBFC - KYC) Directions, 2025 for the paragraphs closest to a plain-English query. Returns '[para ...] text' blocks you may cite."""
        hits = index.search(query, k)
        for hit in hits:
            citation = str(hit.metadata.get("citation", ""))
            if citation and not any(chunk["citation"] == citation and chunk["text"] == hit.page_content for chunk in retrieved):
                retrieved.append({"rule": "search", "citation": citation, "heading": hit.metadata.get("heading", ""), "text": hit.page_content})
        return "\n\n".join(f"[{hit.metadata.get('citation', '')}] {hit.page_content}" for hit in hits) or "No matching paragraph."

    @tool
    def check_kyc_due(risk_category: str, last_kyc_date: str) -> str:
        """Exact periodic-updation maths for RBI para 42(1): give the risk category (low/medium/high) and the last KYC date as DD-MM-YYYY; returns the due date and whether it is overdue on the as-of date."""
        return json.dumps(kyc_due(risk_category, last_kyc_date, as_of))

    @tool
    def compare_across_documents(field: str) -> str:
        """That field's value in each document of the file (name, dob, pan, aadhaar, address or pincode) and whether the documents disagree."""
        return json.dumps(compare_documents(state.get("documents", []), field))

    @tool
    def submit_verdict(decision: str, citations: list[str], reason: str) -> str:
        """Finish: decision is approve, re_kyc or escalate; citations are paragraph references exactly as retrieved; reason is one line."""
        return "recorded"

    return [search_rbi_rules, check_kyc_due, compare_across_documents, submit_verdict]


def _tool_result(tools_by_name: dict[str, Any], call: dict[str, Any]) -> str:
    try:
        return str(tools_by_name[call["name"]].invoke(call["args"]))
    except Exception as exc:  # a bad argument from the model is reported back to it, not raised
        return f"error: {type(exc).__name__}: {exc}"


def _verdict_from_args(args: dict[str, Any]) -> Verdict:
    return Verdict(decision=str(args.get("decision", "")).strip(), citations=list(args.get("citations") or []), reason=str(args.get("reason", "")))


def agent_decide(state: dict[str, Any], model: Any, index: RulesIndex, as_of: date | None = None, max_tool_calls: int = MAX_TOOL_CALLS) -> AgentRun:
    """Run the tool loop for one file. `model` is a chat model that supports bind_tools."""
    retrieved = [dict(chunk) for chunk in state.get("retrieved_rules", [])]
    tools = make_tools(state, index, get_settings().retrieval_k, as_of, retrieved)
    tools_by_name = {t.name: t for t in tools}
    system, human = build_messages(state, as_of)
    messages: list[BaseMessage] = [SystemMessage(content=system[1] + AGENT_INSTRUCTIONS.format(budget=max_tool_calls)),
                                   HumanMessage(content=human[1])]
    run = AgentRun(decision=Decision("", [], ""))
    investigations = 0
    verdict: Verdict | None = None
    for _ in range(max_tool_calls + 3):  # each loop is one model call; submit ends it
        choice = "any" if investigations < max_tool_calls else "submit_verdict"
        bound = model.bind_tools(tools, tool_choice=choice)
        response: AIMessage = with_backoff(lambda: bound.invoke(messages), what="agent LLM call")
        run.llm_calls += 1
        messages.append(response)
        if not response.tool_calls:
            messages.append(HumanMessage(content="Call submit_verdict with your decision now."))
            continue
        for call in response.tool_calls:
            if call["name"] == "submit_verdict":
                missing = missing_required_calls(run.tool_calls)
                if missing and not run.reminded and investigations + len(missing) <= max_tool_calls:
                    run.reminded = True
                    reply = "Not recorded. Before submitting, call: " + "; ".join(missing)
                    run.tool_calls.append(ToolCallRecord("submit_verdict", dict(call["args"]), reply))
                    messages.append(ToolMessage(content=reply, tool_call_id=call["id"]))
                    continue
                verdict = _verdict_from_args(call["args"])
                messages.append(ToolMessage(content="recorded", tool_call_id=call["id"]))
                continue
            if investigations >= max_tool_calls:
                result = "Tool budget exhausted: call submit_verdict now."
                run.budget_exhausted = True
            else:
                investigations += 1
                result = _tool_result(tools_by_name, call)
            run.tool_calls.append(ToolCallRecord(call["name"], dict(call["args"]), result))
            messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        if verdict is not None:
            break
    if verdict is None:
        raise RuntimeError("the agent never called submit_verdict")
    if verdict.decision not in ("approve", "re_kyc", "escalate"):
        raise ValueError(f"agent returned an unknown decision {verdict.decision!r}")
    allowed_state = {**state, "retrieved_rules": retrieved}
    kept, dropped = keep_retrieved_citations(list(verdict.citations), allowed_state, verdict.decision)
    run.decision = Decision(verdict.decision, kept, " ".join(verdict.reason.split()), dropped_citations=dropped)
    run.retrieved = retrieved  # what the agent read, for the audit and later nodes
    logger.info("agent: %s after %d tool calls, %d LLM calls", run.decision.decision, len(run.tool_calls), run.llm_calls)
    return run
