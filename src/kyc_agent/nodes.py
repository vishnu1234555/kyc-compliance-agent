import logging
from collections.abc import Callable
from typing import Any

from kyc_agent.config import get_settings
from kyc_agent.extraction import extract_fields
from kyc_agent.findings import build_findings
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
    logger.info("decide: %d RBI citations", len(state["rbi_citations"]))
    return state


def human_review(state: KYCState) -> KYCState:
    logger.info("human_review: decision=%r", state["decision"])
    return state
