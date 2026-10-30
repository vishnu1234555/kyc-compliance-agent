from datetime import date

from kyc_agent.findings import build_findings, looks_like_placeholder

AS_OF = date(2026, 10, 30)


def _state(fields: dict, errors: list[str] | None = None, documents: list[dict] | None = None) -> dict:
    return {"extracted_fields": fields, "validation_errors": errors or [], "documents": documents or []}


def test_clean_file_has_no_findings() -> None:
    fields = {"name": "MEENAKSHI RAGHAVAN", "pan": "AKLPR4821Q", "form60": False, "doc_type": "passport",
              "risk_category": "medium", "last_kyc_date": "14-02-2022"}
    assert build_findings(_state(fields), as_of=AS_OF) == []


def test_each_rule_is_detected_in_rule_order() -> None:
    fields = {"name": "TEST CUSTOMER", "pan": "KLMPS4521", "form60": False, "doc_type": "utility_bill",
              "pincode": "63200", "dob": "14-05-2012", "risk_category": "high", "last_kyc_date": "15-06-2023"}
    errors = ["PAN format invalid", "Aadhaar must be masked as XXXX-XXXX-1234", "PIN code invalid",
              "Date of birth invalid or applicant under 18"]
    documents = [{"kind": "utility_bill", "name": "TEST CUSTOMER"}, {"kind": "pan_card", "name": "TEST USER"},
                 {"kind": "note", "note": "Customer refused to provide any officially valid document."}]
    findings = build_findings(_state(fields, errors, documents), as_of=AS_OF)
    assert [f.rule for f in findings] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R7", "R8"]
    assert [f.tentative for f in findings] == [False, False, False, False, False, True, False, False, False]
    r4 = findings[3]
    assert r4.details["due_date"] == "15-06-2025" and "due 15-06-2025" in r4.text
    assert findings[4].queries == [] and findings[8].queries == []  # R5 and R8 have no citation
    assert "ANJALI" not in findings[6].text and "utility_bill: TEST CUSTOMER" in findings[6].text


def test_missing_pan_and_missing_identity_document() -> None:
    fields = {"form60": False, "risk_category": "low", "last_kyc_date": "01-01-2024"}
    rules = [f.rule for f in build_findings(_state(fields), as_of=AS_OF)]
    assert rules == ["R1", "R2"]
    with_form60 = {"form60": True, "doc_type": "aadhaar", "risk_category": "low", "last_kyc_date": "01-01-2024"}
    assert build_findings(_state(with_form60), as_of=AS_OF) == []


def test_placeholder_names() -> None:
    assert looks_like_placeholder("TEST CUSTOMER") and looks_like_placeholder("XYZ") and looks_like_placeholder("DEMO USER")
    assert not looks_like_placeholder("MEENAKSHI RAGHAVAN") and not looks_like_placeholder("RAM")
