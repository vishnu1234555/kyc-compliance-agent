import logging
from collections.abc import Callable

from kyc_agent.state import KYCState
from kyc_agent.validators import (
    is_valid_aadhaar_masked,
    is_valid_dob,
    is_valid_pan,
    is_valid_pincode,
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
    logger.info("extract: %d chars of raw document", len(state["raw_document"]))
    return state


def validate(state: KYCState) -> KYCState:
    fields = state["extracted_fields"]
    errors = [
        message
        for name, (check, message) in _CHECKS.items()
        if name in fields and not check(str(fields[name]))
    ]
    logger.info("validate: %d errors across %d fields", len(errors), len(fields))
    return {
        **state,
        "validation_errors": errors,
        "audit_log": [*state["audit_log"], {"node": "validate", "errors": len(errors)}],
    }


def retrieve_rules(state: KYCState) -> KYCState:
    logger.info("retrieve_rules: %d validation errors", len(state["validation_errors"]))
    return state


def decide(state: KYCState) -> KYCState:
    logger.info("decide: %d RBI citations", len(state["rbi_citations"]))
    return state


def human_review(state: KYCState) -> KYCState:
    logger.info("human_review: decision=%r", state["decision"])
    return state
