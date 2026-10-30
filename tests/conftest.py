"""Shared test setup: the unit tests never load the real PII model, Gemini or Chroma."""

import pytest
from langchain_core.documents import Document

from kyc_agent import nodes
from kyc_agent.pii_model import NullReader
from kyc_agent.rules_index import FakeIndex

# One canned chunk per paragraph the policy relies on, worded like the Directions.
RBI_CHUNKS = [
    Document(page_content="(xiv) 'Officially Valid Document (OVD)' means the passport, the driving licence, proof of possession of Aadhaar number, the Voter's Identity Card, the job card that NREGA issues and the letter that the National Population Register issues. Utility bill, property tax receipt, pension payment orders and letter of allotment are deemed OVDs for the limited purpose of proof of address.", metadata={"citation": "para 5(1)(xiv)", "heading": "5. Definitions"}),
    Document(page_content="(1) not open any account in an anonymous or fictitious / benami name.", metadata={"citation": "para 17(1)", "heading": "17. Customer Acceptance Policy"}),
    Document(page_content="(2) open no account where it is unable to apply appropriate CDD measures, either due to non-cooperation of the customer or unreliability of the documents. The NBFC shall consider filing an STR.", metadata={"citation": "para 17(2)", "heading": "17. Customer Acceptance Policy"}),
    Document(page_content="(3) the proof of possession of Aadhaar number where the NBFC cannot carry out the offline verification or any OVD or the equivalent e-document thereof containing the details of their identity and address", metadata={"citation": "para 23(3)", "heading": "23. CDD"}),
    Document(page_content="(5) the PAN or the equivalent e-document thereof or Form No. 60 as defined in Income-tax Rules, 1962", metadata={"citation": "para 23(5)", "heading": "23. CDD"}),
    Document(page_content="Explanation 1: The NBFC shall, where its customer submits a proof of possession of Aadhaar Number containing Aadhaar Number, ensure that such customer redacts or blacks out his Aadhaar number", metadata={"citation": "para 23 Explanation 1", "heading": "23. CDD"}),
    Document(page_content="Explanation 4: Aadhaar number is not mandatory for purposes of KYC.", metadata={"citation": "para 23 Explanation 4", "heading": "23. CDD"}),
    Document(page_content="(1) The NBFC shall carry out periodic updation at least once in every two years for high-risk customers, once in every eight years for medium risk customers and once in every 10 years for low-risk customers from the date of last KYC updation.", metadata={"citation": "para 42(1)", "heading": "42. Updation / Periodic Updation of KYC"}),
]


@pytest.fixture(autouse=True)
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nodes, "default_reader", lambda: NullReader())


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never talk to LangSmith."""
    from kyc_agent import tracing

    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    tracing.langsmith_client.cache_clear()
    yield
    tracing.langsmith_client.cache_clear()


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests run the plain-code decide; a test that wants the LLM path sets DECIDE_MODE=llm and fakes the model."""
    monkeypatch.setenv("DECIDE_MODE", "rules")

    def refuse() -> None:
        raise AssertionError("a unit test tried to build the real Gemini model")

    monkeypatch.setattr(nodes, "get_chat_model", refuse)


@pytest.fixture(autouse=True)
def fake_index(monkeypatch: pytest.MonkeyPatch) -> FakeIndex:
    index = FakeIndex(RBI_CHUNKS)
    monkeypatch.setattr(nodes, "default_index", lambda: index)
    return index
