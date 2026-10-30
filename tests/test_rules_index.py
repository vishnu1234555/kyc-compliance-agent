from pathlib import Path

import pytest
from langchain_core.documents import Document

from kyc_agent import nodes
from kyc_agent.config import DEFAULT_RBI_PDF
from kyc_agent.rules_index import FakeIndex, build_chunks, citation_for, split_by_paragraph, split_long_chunks
from kyc_agent.state import KYCState

POLICY_CITATIONS = ["para 5(1)(xiv)", "para 17(1)", "para 17(2)", "para 23(3)", "para 23(5)",
                    "para 23 Explanation 1", "para 23 Explanation 4", "para 42(1)"]

SMALL = """Introduction text that is never cited.
1. These Directions shall be called the Test Directions.
2. In these Directions:
(1) Terms bearing meaning:
(i) 'Act' means the Act.
Explanation: this stays inside clause (i).
(ii) 'OVD' means the passport.
(2) Other terms.
3. The NBFC shall:
(1) not open anonymous accounts.
(2) open no account where CDD cannot be applied. Rs. 10. is not a paragraph
Explanation 1: numbered explanations are cited on their own.
Explanation 2: so is this one.
"""


def test_split_by_paragraph_metadata() -> None:
    chunks = split_by_paragraph([Document(page_content=SMALL, metadata={"page": 0})])
    citations = [chunk.metadata["citation"] for chunk in chunks]
    assert citations == ["para 1", "para 2", "para 2(1)", "para 2(1)(i)", "para 2(1)(ii)", "para 2(2)", "para 3",
                         "para 3(1)", "para 3(2)", "para 3 Explanation 1", "para 3 Explanation 2"]
    assert "Explanation: this stays" in chunks[3].page_content
    assert chunks[8].page_content.startswith("3. The NBFC shall:\n(2) open no account")  # sub-chunk carries its lead
    assert chunks[0].page_content == "1. These Directions shall be called the Test Directions."
    assert chunks[0].metadata["page"] == 1 and chunks[0].metadata["para"] == "1"
    assert not any("never cited" in chunk.page_content for chunk in chunks)


def test_citation_format() -> None:
    assert citation_for("42", "1") == "para 42(1)"
    assert citation_for("5", "1", "xiv") == "para 5(1)(xiv)"
    assert citation_for("23", "5", "", "1") == "para 23 Explanation 1"


def test_long_paragraphs_are_split_with_metadata_kept() -> None:
    long = Document(page_content="word " * 800, metadata={"citation": "para 9", "para": "9"})
    parts = split_long_chunks([long], chunk_size=1000, chunk_overlap=50)
    assert len(parts) > 1 and all(part.metadata["citation"] == "para 9" for part in parts)


@pytest.mark.skipif(not Path(DEFAULT_RBI_PDF).exists(), reason="Directions PDF not present")
def test_the_real_pdf_yields_every_policy_citation() -> None:
    chunks = build_chunks(Path(DEFAULT_RBI_PDF))
    citations = {chunk.metadata["citation"] for chunk in chunks}
    assert set(POLICY_CITATIONS) <= citations
    paragraphs = {int(chunk.metadata["para"]) for chunk in chunks}
    assert paragraphs == set(range(1, 77))
    ovd = next(chunk for chunk in chunks if chunk.metadata["citation"] == "para 5(1)(xiv)")
    assert "Officially Valid Document" in ovd.page_content


def test_fake_index_ranks_by_word_overlap() -> None:
    index = FakeIndex([Document(page_content="periodic updation of KYC every two years", metadata={"citation": "para 42(1)"}),
                       Document(page_content="PAN or Form No. 60", metadata={"citation": "para 23(5)"})])
    assert [d.metadata["citation"] for d in index.search("periodic updation two years", k=2)] == ["para 42(1)"]
    assert index.search("nothing in common", k=2) == []


def _state(fields: dict, errors: list[str], documents: list[dict] | None = None) -> KYCState:
    return {"raw_document": "", "extracted_fields": fields, "documents": documents or [], "validation_errors": errors,
            "findings": [], "retrieved_rules": [], "rbi_citations": [], "decision": "", "reason": "", "rules_verdict": {}, "audit_log": []}


def test_retrieve_rules_node_cites_in_rule_order(fake_index: FakeIndex) -> None:
    fields = {"name": "SURESH BABU MAHAPATRA", "pan": "STHPM4410G", "form60": False, "doc_type": "pension_order",
              "aadhaar": "XXXX-XXXX-1122", "risk_category": "low", "last_kyc_date": "30-03-2021"}
    out = nodes.retrieve_rules(_state(fields, ["Aadhaar must be masked as XXXX-XXXX-1234"]))
    assert out["rbi_citations"] == ["para 5(1)(xiv)", "para 23(3)", "para 23 Explanation 1"]
    assert [f["rule"] for f in out["findings"]] == ["R1", "R3"]
    assert {chunk["citation"] for chunk in out["retrieved_rules"]} >= set(out["rbi_citations"])
    assert out["audit_log"][-1]["node"] == "retrieve_rules" and out["audit_log"][-1]["citations"] == out["rbi_citations"]
    assert len(fake_index.queries) == 3


def test_retrieve_rules_node_with_nothing_wrong() -> None:
    fields = {"name": "MEENAKSHI RAGHAVAN", "pan": "AKLPR4821Q", "form60": False, "doc_type": "passport",
              "risk_category": "medium", "last_kyc_date": "14-02-2022"}
    out = nodes.retrieve_rules(_state(fields, []))
    assert out["rbi_citations"] == [] and out["findings"] == [] and out["retrieved_rules"] == []


def test_with_backoff_retries_rate_limits_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from kyc_agent import backoff as rules_index

    waits: list[int] = []
    monkeypatch.setattr(rules_index.time, "sleep", waits.append)
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: slow down")
        return "ok"

    assert rules_index.with_backoff(flaky, what="test") == "ok"
    assert waits == [5, 15]
    daily = RuntimeError("429 RESOURCE_EXHAUSTED quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier")
    with pytest.raises(RuntimeError):  # a spent daily quota fails at once instead of waiting four minutes
        rules_index.with_backoff(lambda: (_ for _ in ()).throw(daily), what="test")
    assert waits == [5, 15]
    with pytest.raises(ValueError):
        rules_index.with_backoff(lambda: (_ for _ in ()).throw(ValueError("bad input")), what="test")
