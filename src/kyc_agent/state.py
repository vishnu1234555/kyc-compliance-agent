from typing import Any, Literal, TypedDict

Decision = Literal["approve", "re_kyc", "escalate", ""]


class KYCState(TypedDict):
    raw_document: str
    extracted_fields: dict[str, Any]
    validation_errors: list[str]
    rbi_citations: list[str]
    decision: Decision
    audit_log: list[dict[str, Any]]
