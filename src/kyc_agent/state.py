from typing import Any, Literal, TypedDict

Decision = Literal["approve", "re_kyc", "escalate", ""]


class KYCState(TypedDict):
    raw_document: str
    extracted_fields: dict[str, Any]
    # One entry per document in the file (the identity document, the PAN card, ...), each
    # holding the fields that document shows, so later nodes can compare documents.
    documents: list[dict[str, Any]]
    validation_errors: list[str]
    # retrieve_rules: what is wrong with the file (findings.py) and the RBI paragraphs fetched for it
    findings: list[dict[str, Any]]
    retrieved_rules: list[dict[str, Any]]
    rbi_citations: list[str]
    decision: Decision
    reason: str  # one line from decide
    rules_verdict: dict[str, Any]  # the plain-code baseline (decision, citations, reason, rules)
    audit_log: list[dict[str, Any]]
