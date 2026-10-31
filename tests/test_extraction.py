from kyc_agent import nodes
from kyc_agent.extraction import extract_fields, normalise_address, normalise_date
from kyc_agent.graph import build_graph, run_config
from kyc_agent.pii_model import FakeReader, NullReader, parse_model_output
from kyc_agent.state import KYCState

PASSPORT_FILE = (
    "KYC RECORD - PERIODIC REVIEW\nCustomer ID: NB-10001\nRisk category: Medium\nLast KYC date: 14-02-2022\n\n"
    "[Document 1: Passport]\nREPUBLIC OF INDIA\nPassport No: N8231904\nName: MEENAKSHI RAGHAVAN\n"
    "Date of birth: 15/08/1990\nAddress: 14 Kamaraj Street, Salem, Tamil Nadu\nPIN: 636001\n\n"
    "[Document 2: PAN card]\nINCOME TAX DEPARTMENT\nPermanent Account Number Card\nAKLPR4821Q\nMEENAKSHI RAGHAVAN\n15/08/1990\n"
)
AADHAAR_FILE = (
    "KYC RECORD - PERIODIC REVIEW\nCustomer ID: NB-10014\nRisk category: Medium\nLast KYC date: 25-03-2022\n\n"
    "Government of India\nUnique Identification Authority of India\nVikram Bhosale\nDOB: 06/06/1977\nMALE\n"
    "9999 4512 7782\nAddress: S/O Prakash Bhosale, 51 Dharampeth, Nagpur, Maharashtra\n44OO10\n\n"
    "INCOME TAX DEPARTMENT\nGOVT. OF INDIA\nPermanent Account Number Card\nMQAPBl930D\nVIKRAM BHOSALE\n06/06/1977\n"
)


def test_regex_reader_alone_reads_labelled_values_and_the_header() -> None:
    result = extract_fields(PASSPORT_FILE, NullReader())
    assert result.fields == {
        "name": "MEENAKSHI RAGHAVAN",
        "dob": "15-08-1990",
        "pan": "AKLPR4821Q",
        "form60": False,
        "address": "14 Kamaraj Street, Salem, Tamil Nadu",
        "pincode": "636001",
        "doc_type": "passport",
        "risk_category": "medium",
        "last_kyc_date": "14-02-2022",
    }
    assert "aadhaar" not in result.fields  # left out, never None
    assert [doc["kind"] for doc in result.documents] == ["passport", "pan_card"]
    assert result.documents[1]["pan"] == "AKLPR4821Q" and result.documents[1]["dob"] == "15-08-1990"


def test_unlabelled_name_needs_the_model_and_ocr_noise_is_fixed() -> None:
    regex_only = extract_fields(AADHAAR_FILE, NullReader())
    assert "name" not in regex_only.fields
    assert regex_only.fields["aadhaar"] == "9999-4512-7782"  # as printed, Verhoeff-valid, not masked
    assert regex_only.fields["pincode"] == "440010"
    assert regex_only.fields["pan"] == "MQAPB1930D"

    with_model = extract_fields(
        AADHAAR_FILE,
        FakeReader([("Person Name", "Vikram Bhosale\nDOB", 0.99), ("Person Name", "Prakash Bhosale", 0.98), ("Person Name", "VIKRAM BHOSALE", 0.97)]),
    )
    assert with_model.fields["name"] == "VIKRAM BHOSALE"  # span cut at the line break; the relative inside the address loses to the address
    assert with_model.fields["address"] == "S/O Prakash Bhosale, 51 Dharampeth, Nagpur, Maharashtra"
    assert with_model.documents[1]["name"] == "VIKRAM BHOSALE"


def test_model_candidates_are_structurally_checked() -> None:
    file = PASSPORT_FILE.replace("AKLPR4821Q", "AKLPR4821Q\nAccount 234567890123")
    result = extract_fields(file, FakeReader([("Aadhaar Number", "234567890123", 0.99)]))  # bad checksum
    assert "aadhaar" not in result.fields
    assert result.stats["rejected"] >= 1


def test_wrong_format_pan_on_a_pan_card_is_kept_as_printed() -> None:
    file = PASSPORT_FILE.replace("AKLPR4821Q", "KLMPS4521")
    assert extract_fields(file, NullReader()).fields["pan"] == "KLMPS4521"


def test_form60_only_counts_as_a_document_not_a_mention() -> None:
    note = "KYC RECORD\nCustomer ID: X\nRisk category: Low\nLast KYC date: 01-01-2020\n\n[Branch note dated 12-09-2026]\nCustomer refused to provide PAN or Form 60.\n"
    assert extract_fields(note, NullReader()).fields["form60"] is False
    form = note + "\n[Document 2: Form No. 60]\nFORM NO. 60\nName: A B\n"
    assert extract_fields(form, NullReader()).fields["form60"] is True


def test_normalisers() -> None:
    assert normalise_date("2/3/1990") == "02-03-1990"
    assert normalise_date("1990-03-02") is None
    assert normalise_address("Address: 9 Ashok Rajpath, Patna, Bihar 800001") == "9 Ashok Rajpath, Patna, Bihar"


def test_parse_model_output_handles_the_gliner2_shapes() -> None:
    text = "Name: Arvind Kulkarni lives at 12 MG Road"
    nested = {"entities": {"Person Name": [{"text": "Arvind Kulkarni", "char_start": 6, "char_end": 21, "confidence": 0.98}], "Address": ["12 MG Road"]}}
    cands = parse_model_output(nested, text)
    assert [(c["label"], c["text"], c["start"], c["end"]) for c in cands] == [
        ("Person Name", "Arvind Kulkarni", 6, 21),
        ("Address", "12 MG Road", 31, 41),
    ]
    assert cands[0]["confidence"] == 0.98 and cands[1]["confidence"] is None
    assert parse_model_output({"entities": {"Person Name": ["Nobody Here"]}}, text) == []


def test_extract_node_fills_state_and_audits(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "default_reader", lambda: FakeReader([("Person Name", "Vikram Bhosale", 0.99)]))
    state: KYCState = {"raw_document": AADHAAR_FILE, "extracted_fields": {}, "documents": [], "validation_errors": [], "findings": [], "retrieved_rules": [], "rbi_citations": [], "decision": "", "reason": "", "rules_verdict": {}, "audit_log": []}
    out = nodes.extract(state)
    assert out["extracted_fields"]["name"] == "VIKRAM BHOSALE"
    assert out["extracted_fields"]["aadhaar"] == "9999-4512-7782"
    entry = out["audit_log"][0]
    assert entry["node"] == "extract" and entry["reader"] == "fake" and entry["documents"] == ["aadhaar", "pan_card"]
    assert "9999" not in str(entry)  # the audit trail never carries the number


def test_graph_masks_the_aadhaar_right_after_validate() -> None:
    state: KYCState = {"raw_document": AADHAAR_FILE, "extracted_fields": {}, "documents": [], "validation_errors": [], "findings": [], "retrieved_rules": [], "rbi_citations": [], "decision": "", "reason": "", "rules_verdict": {}, "audit_log": []}
    final = build_graph().invoke(state, config=run_config())
    assert final["validation_errors"] == ["Aadhaar must be masked as XXXX-XXXX-1234"]
    assert final["extracted_fields"]["aadhaar"] == "XXXX-XXXX-7782"
    assert final["documents"][0]["aadhaar"] == "XXXX-XXXX-7782"
    assert final["audit_log"][1] == {"node": "validate", "errors": 1, "aadhaar_masked": True}


def test_a_date_line_above_the_aadhaar_does_not_swallow_it() -> None:
    file = AADHAAR_FILE.replace("DOB: 06/06/1977\nMALE\n9999 4512 7782", "DOB: 06/06/1977\n9999 4512 7782")
    out = extract_fields(file, NullReader())
    assert out.fields["aadhaar"] == "9999-4512-7782" and out.fields["dob"] == "06-06-1977"


def test_reader_descriptions_and_revision_resolution(tmp_path) -> None:
    from kyc_agent.pii_model import GLiNERReader, resolve_revision

    assert NullReader().describe() == "off (regex only)"
    assert FakeReader([]).describe() == "fake"
    reader = GLiNERReader("some/model", 0.3)
    assert reader.describe() == "some/model @ 0.30"  # nothing loaded yet: no revision, no time
    reader.revision, reader.load_seconds = "abc123", 4.8
    assert reader.describe() == "some/model @ 0.30, revision abc123, loaded in 4.8 s"
    assert resolve_revision(str(tmp_path)) == f"local:{tmp_path.name}"
    assert resolve_revision("nobody/no-such-model-cached") == "unknown"
