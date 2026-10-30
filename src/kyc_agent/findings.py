"""What is wrong with a KYC file, as plain sentences with a retrieval query each.

A finding is the bridge between the deterministic nodes and the rules: validate says "PAN
format invalid", a finding says "R2: the PAN on file is in the wrong format" and knows which
sentence of the RBI Directions to look for. retrieve_rules searches with the queries;
rules_decide applies the rules; the LLM reads the sentences.

R6 (a fictitious or benami-looking name) needs judgment, so it is only ever raised as a
*tentative* finding: it fetches para 17(1) for the LLM to weigh and never fires a rule by itself.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from kyc_agent.documents import ADDRESS_ONLY_TYPES
from kyc_agent.rules import compare_across_documents, kyc_due

PAN_ERROR = "PAN format invalid"
AADHAAR_ERROR = "Aadhaar must be masked as XXXX-XXXX-1234"
PIN_ERROR = "PIN code invalid"
DOB_ERROR = "Date of birth invalid or applicant under 18"

QUERIES: dict[str, list[str]] = {
    "R1": [
        "officially valid document OVD means the passport, the driving licence, proof of possession of "
        "Aadhaar number, the Voter's Identity Card, the NREGA job card and the National Population Register "
        "letter; utility bill, property tax receipt, pension payment order and allotment letter are deemed "
        "OVDs for the limited purpose of proof of address only",
        "for customer due diligence the NBFC shall obtain the proof of possession of Aadhaar number or any "
        "OVD or the equivalent e-document containing the details of identity and address",
    ],
    "R2": [
        "for undertaking CDD the NBFC shall obtain from an individual the PAN or the equivalent e-document thereof "
        "or Form No. 60 as defined in Income-tax Rules, 1962"
    ],
    "R3": [
        "where the customer submits a proof of possession of Aadhaar number containing the Aadhaar number, the NBFC "
        "shall ensure that the customer redacts or blacks out the Aadhaar number where authentication of Aadhaar is not required"
    ],
    "R4": [
        "periodic updation of KYC at least once in every two years for high-risk customers, once in every eight "
        "years for medium risk customers and once in every ten years for low-risk customers from the date of last KYC updation"
    ],
    "R5": [],
    "R6": ["the NBFC shall not open any account in an anonymous or fictitious or benami name"],
    "R7": [
        "open no account where the NBFC is unable to apply appropriate customer due diligence measures due to "
        "non-cooperation of the customer or unreliability of the documents; consider filing a suspicious transaction report"
    ],
    "R8": [],
}

_PLACEHOLDER_WORDS = {
    "TEST", "TESTING", "CUSTOMER", "DEMO", "USER", "SAMPLE", "DUMMY", "XYZ", "ABC", "UNKNOWN", "NA", "NONE",
    "NULL", "ANONYMOUS", "FAKE", "EXAMPLE", "FOO", "BAR", "TEMP", "ASDF", "QWERTY",
}
_REFUSAL = re.compile(r"\brefus|declin\w+ to (?:provide|give|submit)|did not (?:provide|submit)|unwilling to", re.I)


@dataclass
class Finding:
    rule: str  # R1..R8
    text: str  # one plain-English line
    queries: list[str] = field(default_factory=list)
    tentative: bool = False  # true only for R6: needs judgment, never fires on its own
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def looks_like_placeholder(name: str) -> bool:
    """TEST CUSTOMER, XYZ, DEMO USER: a hint for retrieval, not a verdict."""
    tokens = re.findall(r"[A-Z]+", name.upper())
    return not tokens or any(token in _PLACEHOLDER_WORDS for token in tokens)


def _finding(rule: str, text: str, tentative: bool = False, **details: Any) -> Finding:
    return Finding(rule=rule, text=text, queries=list(QUERIES[rule]), tentative=tentative, details=details)


def build_findings(state: dict[str, Any], as_of: date | None = None) -> list[Finding]:
    """Every rule whose trigger the deterministic nodes can see, in rule order (R1 first)."""
    fields = state.get("extracted_fields", {})
    errors = state.get("validation_errors", [])
    documents = state.get("documents", [])
    found: list[Finding] = []

    doc_type = fields.get("doc_type")
    if doc_type is None:
        found.append(_finding("R1", "No identity document on file at all, so no officially valid document (OVD)", doc_type=None))
    elif doc_type in ADDRESS_ONLY_TYPES:
        found.append(_finding("R1", f"The identity document is a {doc_type.replace('_', ' ')}, which proves address only; no OVD on file", doc_type=doc_type))

    if PAN_ERROR in errors:
        found.append(_finding("R2", f"The PAN on file is in the wrong format: {fields.get('pan')}", pan=fields.get("pan")))
    elif fields.get("pan") is None and not fields.get("form60"):
        found.append(_finding("R2", "No PAN and no Form 60 anywhere in the file"))

    if AADHAAR_ERROR in errors:
        found.append(_finding("R3", "An Aadhaar number is visible in full in the file; it must be redacted"))

    risk, last_kyc = fields.get("risk_category"), fields.get("last_kyc_date")
    if risk and last_kyc:
        due = kyc_due(risk, last_kyc, as_of)
        if due.get("overdue"):
            found.append(_finding(
                "R4",
                f"Periodic KYC updation overdue: {risk} risk, last KYC {last_kyc}, was due {due['due_date']}, as of {due['as_of']}",
                **due,
            ))

    if PIN_ERROR in errors:
        found.append(_finding("R5", f"The PIN code on file is not six digits starting 1-9: {fields.get('pincode')}", pincode=fields.get("pincode")))

    name = fields.get("name")
    if name and looks_like_placeholder(name):
        found.append(_finding("R6", f"The name may be fictitious or a placeholder: {name!r} (needs judgment)", tentative=True, name=name))

    for key in ("name", "dob"):
        comparison = compare_across_documents(documents, key)
        if comparison["mismatch"]:
            shown = "; ".join(f"{entry['document']}: {entry['value']}" for entry in comparison["values"])
            found.append(_finding("R7", f"The {key} differs between documents in the file ({shown}); CDD cannot be completed", **comparison))
    for document in documents:
        note = document.get("note", "")
        if document.get("kind") == "note" and _REFUSAL.search(note):
            found.append(_finding("R7", "The file records that the customer refused to provide documents; CDD cannot be completed", note=note[:300]))
            break

    if DOB_ERROR in errors:
        found.append(_finding("R8", f"Applicant is under 18 on the as-of date, or the date of birth is invalid: {fields.get('dob')}", dob=fields.get("dob")))

    return found
