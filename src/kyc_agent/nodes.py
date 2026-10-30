import logging
from collections.abc import Callable
from typing import Any

from langgraph.types import interrupt

from kyc_agent.agent import agent_decide
from kyc_agent.config import get_settings
from kyc_agent.decide import Decision, llm_decide, rules_decide
from kyc_agent.extraction import extract_fields
from kyc_agent.findings import build_findings
from kyc_agent.llm import get_chat_model
from kyc_agent.pii_model import default_reader
from kyc_agent.rules_index import default_index
from kyc_agent.state import KYCState
from kyc_agent.validators import (
    is_valid_aadhaar_masked,
    is_valid_dob,
    is_valid_pan,
    is_valid_pincode,
    mask_aadhaar,
)

logger = logging.getLogger(__name__)

# field name in extracted_fields -> (check, plain-English failure message)
_CHECKS: dict[str, tuple[Callable[[str], bool], str]] = {
    "pan": (is_valid_pan, "PAN format invalid"),
    "aadhaar": (is_valid_aadhaar_masked, "Aadhaar must be masked as XXXX-XXXX-1234"),
    "pincode": (is_valid_pincode, "PIN code invalid"),
    "dob": (is_valid_dob, "Date of birth invalid or applicant under 18"),
}


def extract(state: KYCState) -> KYCState:
    reader = default_reader()
    result = extract_fields(state["raw_document"], reader)
    logger.info("extract: %d chars, reader %s, fields %s", len(state["raw_document"]), reader.name, sorted(result.fields))
    entry = {
        "node": "extract",
        "reader": reader.name,
        "customer_id": result.stats.get("customer_id"),
        "documents": [document["kind"] for document in result.documents],
        "fields": sorted(result.fields),
        "candidates": {key: result.stats[key] for key in ("regex_candidates", "model_candidates", "kept", "rejected")},
    }
    return {
        **state,
        "extracted_fields": result.fields,
        "documents": result.documents,
        "audit_log": [*state["audit_log"], entry],
    }


def _masked(fields: dict[str, Any]) -> dict[str, Any]:
    if "aadhaar" in fields:
        return {**fields, "aadhaar": mask_aadhaar(str(fields["aadhaar"]))}
    return fields


def validate(state: KYCState) -> KYCState:
    fields = state["extracted_fields"]
    errors = [
        message
        for name, (check, message) in _CHECKS.items()
        if name in fields and not check(str(fields[name]))
    ]
    # From here on the state keeps only the masked Aadhaar (RBI para 23 Explanation 1); the
    # error message above is the record that the file showed it in full.
    masked_fields = _masked(fields)
    masked_documents = [_masked(document) for document in state.get("documents", [])]
    aadhaar_masked = masked_fields != fields or masked_documents != state.get("documents", [])
    logger.info("validate: %d errors across %d fields", len(errors), len(fields))
    return {
        **state,
        "extracted_fields": masked_fields,
        "documents": masked_documents,
        "validation_errors": errors,
        "audit_log": [*state["audit_log"], {"node": "validate", "errors": len(errors), "aadhaar_masked": aadhaar_masked}],
    }


def retrieve_rules(state: KYCState) -> KYCState:
    """Turn the file's problems into findings, fetch the RBI paragraphs for each, cite the best hit."""
    findings = build_findings(state)
    index = default_index()
    k = get_settings().retrieval_k
    citations: list[str] = []
    retrieved: list[dict[str, Any]] = []
    seen_chunks: set[tuple[str, str]] = set()
    queries = 0
    for finding in findings:
        for query in finding.queries:
            queries += 1
            hits = index.search(query, k)
            for rank, hit in enumerate(hits):
                citation = str(hit.metadata.get("citation", ""))
                if (citation, hit.page_content) not in seen_chunks:
                    seen_chunks.add((citation, hit.page_content))
                    retrieved.append({"rule": finding.rule, "rank": rank, "citation": citation,
                                      "heading": hit.metadata.get("heading", ""), "text": hit.page_content})
                if rank == 0 and citation and citation not in citations:
                    citations.append(citation)
    logger.info("retrieve_rules: %d findings, %d queries, citations %s", len(findings), queries, citations)
    entry = {"node": "retrieve_rules", "index": index.name, "findings": [f.rule + ("?" if f.tentative else "") for f in findings],
             "queries": queries, "chunks": len(retrieved), "citations": citations}
    return {
        **state,
        "findings": [finding.as_dict() for finding in findings],
        "retrieved_rules": retrieved,
        "rbi_citations": citations,
        "audit_log": [*state["audit_log"], entry],
    }


def decide(state: KYCState) -> KYCState:
    """Rules baseline always; on top of it the LLM verdict (DECIDE_MODE=llm) or the tool-using agent
    (DECIDE_MODE=agent). When the model cannot be reached the rules verdict stands, and the audit says so."""
    settings = get_settings()
    baseline = rules_decide(state)
    entry: dict[str, Any] = {"node": "decide", "mode": settings.decide_mode, "rules_baseline": baseline.decision,
                             "rules_fired": baseline.rules}
    verdict = baseline
    retrieved = state.get("retrieved_rules", [])
    if settings.decide_mode in ("llm", "agent"):
        entry["model"] = settings.gemini_model
        try:
            if settings.decide_mode == "agent":
                run = agent_decide(state, get_chat_model(), default_index())
                verdict = run.decision
                retrieved = run.retrieved
                entry["tool_calls"] = [record.as_dict() for record in run.tool_calls]
                entry["llm_calls"] = run.llm_calls
                entry["budget_exhausted"] = run.budget_exhausted
            else:
                verdict = llm_decide(state, get_chat_model())
            entry["dropped_citations"] = verdict.dropped_citations
        except Exception as exc:  # the model is unreachable even after backoff: keep the file moving
            logger.error("decide: LLM failed (%s); applying the rules baseline", exc)
            entry["llm_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            verdict = Decision(baseline.decision, baseline.citations,
                               f"LLM unavailable, rules baseline applied: {baseline.reason}", rules=baseline.rules,
                               error=entry["llm_error"])
    entry.update({"decision": verdict.decision, "citations": verdict.citations})
    logger.info("decide: %s (rules baseline %s) citations %s", verdict.decision, baseline.decision, verdict.citations)
    return {
        **state,
        "retrieved_rules": retrieved,
        "decision": verdict.decision,
        "rbi_citations": verdict.citations,
        "reason": verdict.reason,
        "rules_verdict": baseline.as_dict(),
        "audit_log": [*state["audit_log"], entry],
    }


def human_review(state: KYCState) -> KYCState:
    """Pause for a reviewer (LangGraph interrupt). The graph stops here until it is resumed with
    Command(resume={...}); the checkpointer keeps the state in between.

    The resume value is a dict: {"reviewer": str, "action": "confirm" | "override", "decision":
    optional new decision, "note": optional text, "auto": True when the evals resumed it}.
    """
    review: dict[str, Any] = interrupt({
        "customer_id": next((entry.get("customer_id") for entry in state["audit_log"] if entry.get("node") == "extract"), None),
        "decision": state["decision"],
        "reason": state.get("reason", ""),
        "citations": state["rbi_citations"],
        "validation_errors": state["validation_errors"],
        "question": "Confirm the decision, or override it (approve / re_kyc / escalate) with a note.",
    }) or {}
    action = str(review.get("action", "confirm"))
    decision = state["decision"]
    if action == "override" and review.get("decision") in ("approve", "re_kyc", "escalate"):
        decision = review["decision"]
    reason = state.get("reason", "")
    if review.get("note"):
        reason = f"{reason} Reviewer: {review['note']}".strip()
    logger.info("human_review: %s by %s -> %s", action, review.get("reviewer", "unknown"), decision)
    entry = {"node": "human_review", "reviewer": review.get("reviewer", "unknown"), "action": action,
             "decision_before": state["decision"], "decision": decision, "auto": bool(review.get("auto", False))}
    return {**state, "decision": decision, "reason": reason, "audit_log": [*state["audit_log"], entry]}
