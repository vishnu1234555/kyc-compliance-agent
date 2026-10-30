"""Ported from backend/test_validation.py of the Aadhaar-Final project, plus the KYC changes."""

from kyc_agent.arbitration import (
    Candidate,
    arbitrate,
    is_structurally_valid,
    priority,
    validate_and_arbitrate,
    verhoeff_valid,
)


def _aadhaar_with_valid_checksum(base11: str) -> str:
    for digit in "0123456789":
        if verhoeff_valid(base11 + digit):
            return base11 + digit
    raise AssertionError("no valid check digit exists")


VALID_AADHAAR = _aadhaar_with_valid_checksum("23456789012")


def _span(label: str, text: str, start: int, end: int, conf: float = 0.9, source: str = "model", trusted: bool = False) -> Candidate:
    return {"label": label, "text": text, "start": start, "end": end, "confidence": conf, "source": source, "trusted": trusted}


def test_verhoeff_accepts_valid_and_rejects_mutation() -> None:
    assert verhoeff_valid(VALID_AADHAAR)
    mutated = VALID_AADHAAR[:5] + str((int(VALID_AADHAAR[5]) + 1) % 10) + VALID_AADHAAR[6:]
    assert not verhoeff_valid(mutated)


def test_aadhaar_rejects_bad_checksum_and_reserved_leading_digits() -> None:
    assert is_structurally_valid("Aadhaar Number", VALID_AADHAAR)
    assert not is_structurally_valid("Aadhaar Number", "234567890123")
    for bad in ("0234 5678 9012", "1234 5678 9012"):
        assert not is_structurally_valid("Aadhaar Number", bad)


def test_aadhaar_accepts_spaced_hyphenated_and_masked_forms() -> None:
    d = VALID_AADHAAR
    assert is_structurally_valid("Aadhaar Number", f"{d[0:4]} {d[4:8]} {d[8:12]}")
    assert is_structurally_valid("Aadhaar Number", f"{d[0:4]}-{d[4:8]}-{d[8:12]}")
    assert is_structurally_valid("Aadhaar Number", "XXXX XXXX 1234")  # KYC change: masked is valid
    assert is_structurally_valid("Aadhaar Number", "XXXX-XXXX-1234")


def test_pan_holder_type_character() -> None:
    assert is_structurally_valid("PAN Number", "ABCPE1234F")
    assert not is_structurally_valid("PAN Number", "ABCZE1234F")
    assert not is_structurally_valid("PAN Number", "KLMPS4521")


def test_account_number_is_the_weakest_claim() -> None:
    assert priority("Account Number") > priority("Aadhaar Number")
    assert priority("Account Number") > priority("PAN Number")
    assert priority("Address") > priority("Aadhaar Number")  # free text never outranks an ID


def test_aadhaar_beats_account_number_on_the_same_span() -> None:
    kept = arbitrate([
        _span("Account Number", VALID_AADHAAR, 0, 12, conf=0.99),
        _span("Aadhaar Number", VALID_AADHAAR, 0, 12, conf=0.55),
    ])
    assert [cand["label"] for cand in kept] == ["Aadhaar Number"]


def test_labelled_address_beats_a_name_printed_inside_it() -> None:
    kept = arbitrate([
        _span("Person Name", "Prakash Kulkarni", 4, 20, conf=0.99),
        _span("Address", "S/O Prakash Kulkarni, 221 Koramangala", 0, 37, conf=None, source="regex", trusted=True),
    ])
    assert [cand["label"] for cand in kept] == ["Address"]


def test_a_guessed_address_never_drops_a_labelled_name() -> None:
    kept = arbitrate([
        _span("Address", "Elector's Name: XYZ Father's Name: ABC", 0, 38, conf=0.4),
        _span("Person Name", "XYZ", 16, 19, conf=None, source="regex", trusted=True),
    ])
    assert [cand["label"] for cand in kept] == ["Person Name"]


def test_trusted_candidate_wins_the_same_span_and_skips_the_structure_check() -> None:
    kept, rejected = validate_and_arbitrate([
        _span("PAN Number", "KLMPS4521", 0, 9, conf=0.99),  # model: malformed, dropped
        _span("PAN Number", "KLMPS4521", 0, 9, conf=None, source="regex", trusted=True),  # printed on the PAN card
    ])
    assert [cand["source"] for cand in kept] == ["regex"]
    assert rejected[0]["reason"] == "failed PAN Number structure check"


def test_invalid_candidates_are_dropped_with_a_reason_and_results_are_in_reading_order() -> None:
    kept, rejected = validate_and_arbitrate([
        _span("IFSC Code", "HDFC0001234", 40, 51),
        _span("Aadhaar Number", "234567890123", 0, 12),
        _span("PAN Number", "ABCPE1234F", 20, 30),
    ])
    assert [cand["start"] for cand in kept] == [20, 40]
    assert len(rejected) == 1 and "structure check" in rejected[0]["reason"]
