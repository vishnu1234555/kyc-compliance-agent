import logging

from kyc_agent.state import KYCState

logger = logging.getLogger(__name__)


def extract(state: KYCState) -> KYCState:
    logger.info("extract: %d chars of raw document", len(state["raw_document"]))
    return state


def validate(state: KYCState) -> KYCState:
    logger.info("validate: %d extracted fields", len(state["extracted_fields"]))
    return state


def retrieve_rules(state: KYCState) -> KYCState:
    logger.info("retrieve_rules: %d validation errors", len(state["validation_errors"]))
    return state


def decide(state: KYCState) -> KYCState:
    logger.info("decide: %d RBI citations", len(state["rbi_citations"]))
    return state


def human_review(state: KYCState) -> KYCState:
    logger.info("human_review: decision=%r", state["decision"])
    return state
