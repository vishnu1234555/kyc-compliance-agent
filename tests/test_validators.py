from datetime import date

import pytest

from kyc_agent.nodes import validate
from kyc_agent.state import KYCState
from kyc_agent.validators import (
    is_valid_aadhaar_masked,
    is_valid_dob,
    is_valid_pan,
    is_valid_pincode,
    mask_aadhaar,
)

TODAY = date(2026, 10, 30)


@pytest.mark.parametrize("pan", ["ABCDE1234F", "ZZZZZ0000Z"])
def test_pan_valid(pan: str) -> None:
    assert is_valid_pan(pan)


@pytest.mark.parametrize("pan", ["abcde1234f", "ABCD12345F", "ABCDE1234", "ABCDE1234F1"])
def test_pan_invalid(pan: str) -> None:
    assert not is_valid_pan(pan)


@pytest.mark.parametrize("value", ["XXXX-XXXX-1234", "XXXX-XXXX-0000"])
def test_aadhaar_masked_valid(value: str) -> None:
    assert is_valid_aadhaar_masked(value)


@pytest.mark.parametrize("value", ["123456781234", "XXXX-XXXX-12", "xxxx-xxxx-1234", "XXXXXXXX1234"])
def test_aadhaar_masked_invalid(value: str) -> None:
    assert not is_valid_aadhaar_masked(value)


@pytest.mark.parametrize("pin", ["560001", "110092"])
def test_pincode_valid(pin: str) -> None:
    assert is_valid_pincode(pin)


@pytest.mark.parametrize("pin", ["060001", "56001", "5600011", "56000A"])
def test_pincode_invalid(pin: str) -> None:
    assert not is_valid_pincode(pin)


@pytest.mark.parametrize("dob", ["15-08-1990", "30-10-2008"])  # second turns 18 today
def test_dob_valid(dob: str) -> None:
    assert is_valid_dob(dob, today=TODAY)


@pytest.mark.parametrize("dob", ["31-10-2008", "31-02-2000", "1-1-2000", "2000-01-01"])
def test_dob_invalid(dob: str) -> None:
    assert not is_valid_dob(dob, today=TODAY)


def test_validate_node_collects_errors_and_audits() -> None:
    state: KYCState = {
        "raw_document": "",
        "extracted_fields": {"pan": "bad", "aadhaar": "XXXX-XXXX-1234", "pincode": "012345"},
        "documents": [],
        "validation_errors": [],
        "findings": [],
        "retrieved_rules": [],
        "rbi_citations": [],
        "decision": "",
        "reason": "",
        "rules_verdict": {},
        "audit_log": [],
    }
    out = validate(state)
    assert out["validation_errors"] == ["PAN format invalid", "PIN code invalid"]
    assert out["audit_log"] == [{"node": "validate", "errors": 2, "aadhaar_masked": False}]
    assert state["audit_log"] == []  # input not mutated


def test_validate_node_masks_a_full_aadhaar_but_keeps_the_error() -> None:
    state: KYCState = {
        "raw_document": "",
        "extracted_fields": {"aadhaar": "9999-4512-7782"},
        "documents": [{"kind": "aadhaar", "aadhaar": "9999-4512-7782"}],
        "validation_errors": [],
        "findings": [],
        "retrieved_rules": [],
        "rbi_citations": [],
        "decision": "",
        "reason": "",
        "rules_verdict": {},
        "audit_log": [],
    }
    out = validate(state)
    assert out["validation_errors"] == ["Aadhaar must be masked as XXXX-XXXX-1234"]
    assert out["extracted_fields"] == {"aadhaar": "XXXX-XXXX-7782"}
    assert out["documents"] == [{"kind": "aadhaar", "aadhaar": "XXXX-XXXX-7782"}]
    assert out["audit_log"][-1]["aadhaar_masked"] is True
    assert mask_aadhaar("XXXX-XXXX-1234") == "XXXX-XXXX-1234"
