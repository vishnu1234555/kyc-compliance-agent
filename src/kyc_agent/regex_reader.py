"""Reader 1 of 2: regex and plain parsing for the values that have a fixed shape.

It reads labelled lines ("Name: ...", "PIN Code: ..."), the ID numbers it can recognise by
shape (PAN, masked and unmasked Aadhaar) and the bare lines that cards print without a label
(the number on a PAN card, a date on its own line, a six-digit PIN under an address). Names
and addresses without a label are left to the PII model (reader 2, pii_model.py).

Every value comes out as a Candidate with absolute offsets into the text it was read from, so
arbitration.py can merge it with the model's candidates. ``trusted`` marks values the
document itself labels; they are kept as printed even when malformed.
"""

import re

from kyc_agent.arbitration import Candidate
from kyc_agent.documents import Block

DATE = r"\d{1,2}[-/.]\d{1,2}[-/.]\d{4}"
AADHAAR_ANY = r"X{4}[\s-]?X{4}[\s-]?\d{4}|\d{4}[\s-]?\d{4}[\s-]?\d{4}"

# label -> the words a document prints before the colon
_LINE_LABELS: dict[str, str] = {
    "Relative Name": r"(?:father|mother|husband|wife|spouse|guardian)'?s?\s+name|guardian",
    "Person Name": (
        r"elector'?s\s+name|name\s+of\s+(?:the\s+)?(?:applicant|customer|holder|account\s+holder|pensioner)"
        r"|(?:applicant|customer|holder|account\s+holder)\s+name|pensioner|owner|name"
    ),
    "Date Of Birth": r"date\s+of\s+birth|d\.?o\.?b\.?|birth\s+date",
    "PAN Number": r"permanent\s+account\s+number|pan\s+(?:no\.?|number|card\s+no\.?)|pan",
    "Aadhaar Number": r"aadha?ar\s*(?:no\.?|number)?|uid\s*(?:no\.?|number)?",
    "PIN Code": r"pin\s*code|pincode|pin|postal\s+code|zip\s*code",
    "Address": r"(?:service|property|permanent|present|residential|communication|current|registered)\s+address|address",
}
_LINE = {
    label: re.compile(rf"^[ \t]*(?:{words})[ \t]*[:\-][ \t]*(?P<value>.+?)[ \t]*$", re.I | re.M)
    for label, words in _LINE_LABELS.items()
}
# how much of a labelled line's value is the value
_VALUE_SHAPE: dict[str, re.Pattern[str]] = {
    "Date Of Birth": re.compile(DATE),
    "PAN Number": re.compile(r"[A-Z0-9]{6,12}"),
    "Aadhaar Number": re.compile(AADHAAR_ANY),
    "PIN Code": re.compile(r"\d{4,7}"),
}

_DOB_IN_SENTENCE = re.compile(rf"(?:date\s+of\s+birth|dob)\s*[:\-]?\s*(?P<value>{DATE})", re.I)
_BARE_DATE_LINE = re.compile(rf"^[ \t]*(?P<value>{DATE})[ \t]*$", re.M)
_PAN_ANYWHERE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PAN_CARD_LINE = re.compile(r"^[ \t]*(?P<value>[A-Z]{5}\d{3,4}[A-Z]?)[ \t]*$", re.M)
_AADHAAR_ANYWHERE = re.compile(rf"\b(?:{AADHAAR_ANY})\b")
_BARE_PIN_LINE = re.compile(r"^[ \t]*(?P<value>\d{5,6})[ \t]*$", re.M)
_TRAILING_PIN = re.compile(r"[ \t,]*(?P<value>\b\d{6})[ \t]*$")
_RELATIVE_IN_TEXT = re.compile(
    r"(?:\b[SWDC]/[Oo]\.?|\bguardian)[ \t]+(?P<value>[A-Z][A-Za-z.]*(?:[ \t]+[A-Z][A-Za-z.]*){0,3})"
)


def _candidate(label: str, text: str, start: int, trusted: bool) -> Candidate:
    return {
        "label": label,
        "text": text,
        "start": start,
        "end": start + len(text),
        "confidence": None,
        "source": "regex",
        "trusted": trusted,
    }


def _labelled_lines(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for label, pattern in _LINE.items():
        for match in pattern.finditer(text):
            value = match.group("value")
            start = match.start("value")
            shape = _VALUE_SHAPE.get(label)
            if shape is not None:
                inner = shape.search(value)
                if inner is None:
                    continue
                value, start = inner.group(), start + inner.start()
            if label == "Address":
                trailing = _TRAILING_PIN.search(value)
                if trailing:
                    out.append(_candidate("PIN Code", trailing.group("value"), start + trailing.start("value"), False))
                    value = value[: trailing.start()].rstrip(" ,")
            out.append(_candidate(label, value, start, True))
    return out


def _sweeps(text: str, blocks: list[Block]) -> list[Candidate]:
    out: list[Candidate] = []
    for match in _DOB_IN_SENTENCE.finditer(text):
        out.append(_candidate("Date Of Birth", match.group("value"), match.start("value"), True))
    for match in _BARE_DATE_LINE.finditer(text):
        out.append(_candidate("Date Of Birth", match.group("value"), match.start("value"), False))
    for match in _PAN_ANYWHERE.finditer(text):
        out.append(_candidate("PAN Number", match.group(), match.start(), False))
    for block in blocks:
        if block.kind != "pan_card":
            continue
        for match in _PAN_CARD_LINE.finditer(block.text):
            # The bare token on a PAN card is the PAN, however many characters it has.
            out.append(_candidate("PAN Number", match.group("value"), block.start + match.start("value"), True))
    for match in _AADHAAR_ANYWHERE.finditer(text):
        out.append(_candidate("Aadhaar Number", match.group(), match.start(), False))
    for match in _BARE_PIN_LINE.finditer(text):
        out.append(_candidate("PIN Code", match.group("value"), match.start("value"), False))
    for match in _RELATIVE_IN_TEXT.finditer(text):
        out.append(_candidate("Relative Name", match.group("value"), match.start("value"), True))
    return out


def _inside_an_address(cand: Candidate, addresses: list[Candidate]) -> bool:
    return any(a["start"] <= cand["start"] and cand["end"] <= a["end"] for a in addresses)


def regex_candidates(text: str, blocks: list[Block]) -> list[Candidate]:
    """Every value the regex reader can see in `text`, one candidate per (label, span)."""
    unique: dict[tuple[str, int, int], Candidate] = {}
    for cand in _labelled_lines(text) + _sweeps(text, blocks):
        key = (cand["label"], cand["start"], cand["end"])
        if key not in unique or (cand["trusted"] and not unique[key]["trusted"]):
            unique[key] = cand
    # "S/O Prakash Kulkarni" inside a labelled address is part of the address, not a separate
    # value; the address candidate already keeps a model name from claiming it.
    addresses = [c for c in unique.values() if c["label"] == "Address"]
    found = [c for c in unique.values() if not (c["label"] == "Relative Name" and _inside_an_address(c, addresses))]
    return sorted(found, key=lambda c: (c["start"], c["label"]))
