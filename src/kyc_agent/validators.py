"""Deterministic field checks for Indian KYC documents. No I/O, no LLM."""

import re
from datetime import date, datetime

_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AADHAAR_MASKED = re.compile(r"^XXXX-XXXX-[0-9]{4}$")
_PINCODE = re.compile(r"^[1-9][0-9]{5}$")
_DOB = re.compile(r"^[0-9]{2}-[0-9]{2}-[0-9]{4}$")


def is_valid_pan(pan: str) -> bool:
    return _PAN.fullmatch(pan) is not None


def is_valid_aadhaar_masked(value: str) -> bool:
    return _AADHAAR_MASKED.fullmatch(value) is not None


def is_valid_pincode(pin: str) -> bool:
    return _PINCODE.fullmatch(pin) is not None


def is_valid_dob(dob: str, today: date | None = None) -> bool:
    if _DOB.fullmatch(dob) is None:
        return False
    try:
        born = datetime.strptime(dob, "%d-%m-%Y").date()
    except ValueError:
        return False
    today = today or date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age >= 18


def mask_aadhaar(value: str) -> str:
    """XXXX-XXXX-<last four> for a full 12-digit Aadhaar number; anything else comes back unchanged.

    RBI para 23 Explanation 1 asks for the number to be redacted, so once validate has seen
    it, nothing downstream (state, traces, the LLM) gets the full number.
    """
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12:
        return f"XXXX-XXXX-{digits[-4:]}"
    return value
