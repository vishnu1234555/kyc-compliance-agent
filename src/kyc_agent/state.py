from typing import Any, Literal, TypedDict

Decision = Literal["approve", "re_kyc", "escalate", ""]


class KYCState(TypedDict):
    raw_document: str
    extracted_fields: dict[str, Any]
    # One entry per document in the file (the identity document, the PAN card, ...), each
    # holding the fields that document shows, so later nodes can compare documents.
    documents: list[dict[str, Any]]
    validation_errors: list[str]
    rbi_citations: list[str]
    decision: Decision
    audit_log: list[dict[str, Any]]
