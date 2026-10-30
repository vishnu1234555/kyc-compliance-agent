"""Where the two readers meet: regex (regex_reader.py) and the PII model (pii_model.py).

1. Fix OCR digit noise, split the file into header + documents.
2. Read regex candidates over the whole text; run the PII model on each document.
3. Validate and arbitrate all candidates together (arbitration.py).
4. Per document, pick each field's best surviving candidate; then take every field from the
   first document that shows it (evals/POLICY.md section 4), and check the result against
   the Pydantic schema.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kyc_agent.arbitration import Candidate, validate_and_arbitrate
from kyc_agent.documents import Block, doc_type_of, has_form60, split_document
from kyc_agent.ocr import fix_digit_noise
from kyc_agent.pii_model import PIIReader
from kyc_agent.regex_reader import regex_candidates
from kyc_agent.schema import ExtractedFields

logger = logging.getLogger(__name__)

DOCUMENT_FIELDS = ("name", "dob", "pan", "aadhaar", "address", "pincode")
_LABEL_FOR = {
    "name": "Person Name",
    "dob": "Date Of Birth",
    "pan": "PAN Number",
    "aadhaar": "Aadhaar Number",
    "address": "Address",
    "pincode": "PIN Code",
}
_TRAILING_PIN = re.compile(r"[\s,]*\b\d{6}\s*$")


@dataclass
class Extraction:
    fields: dict[str, Any]
    documents: list[dict[str, Any]]
    stats: dict[str, Any] = field(default_factory=dict)


def normalise_date(text: str) -> str | None:
    """'15/08/1990' or '2-3-1990' -> '15-08-1990'; None when the shape is wrong."""
    match = re.fullmatch(r"\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*", text or "")
    if match is None:
        return None
    day, month, year = (int(part) for part in match.groups())
    return f"{day:02d}-{month:02d}-{year:04d}"


def normalise_name(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .,:-").upper()


def normalise_aadhaar(text: str) -> str:
    cleaned = text.strip().upper()
    if cleaned.startswith("X"):
        return f"XXXX-XXXX-{cleaned[-4:]}"
    digits = re.sub(r"\D", "", cleaned)
    return f"{digits[0:4]}-{digits[4:8]}-{digits[8:12]}"


def normalise_address(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^address\s*[:\-]\s*", "", cleaned, flags=re.I)
    cleaned = _TRAILING_PIN.sub("", cleaned)
    return cleaned.strip(" ,")


def normalise_plain(text: str) -> str:
    return text.strip()


_NORMALISE = {
    "name": normalise_name,
    "dob": normalise_date,
    "pan": lambda text: text.strip().upper(),
    "aadhaar": normalise_aadhaar,
    "address": normalise_address,
    "pincode": normalise_plain,
}


def _rank(cand: Candidate) -> tuple[int, float, int]:
    # What the document labels itself, then other regex finds (in reading order), then the
    # model's candidates, most confident first.
    tier = 0 if cand.get("trusted") else 1 if cand.get("source") == "regex" else 2
    return (tier, -float(cand.get("confidence") or 0.0), cand["start"])


def fields_for_block(block: Block, kept: list[Candidate]) -> dict[str, Any]:
    """The fields one document shows, from the candidates that survived arbitration inside it."""
    inside = [cand for cand in kept if block.contains(cand["start"])]
    out: dict[str, Any] = {"kind": block.kind, "title": block.title}
    for name, label in _LABEL_FOR.items():
        for cand in sorted((c for c in inside if c["label"] == label), key=_rank):
            value = _NORMALISE[name](cand["text"])
            if value:
                out[name] = value
                break
    return out


def _model_candidates(blocks: list[Block], reader: PIIReader) -> list[Candidate]:
    out: list[Candidate] = []
    for block in blocks:
        for cand in reader.read(block.text):
            # On a card every value sits on one line; the model sometimes runs a span into the
            # next line ("Lakshmi Narayanan\nDOB"), so cut it there. A PIN at the end of an
            # address span would collide with the PIN candidate, so cut that too.
            text = cand["text"].split("\n", 1)[0]
            if cand["label"] == "Address":
                text = _TRAILING_PIN.sub("", text)
            text = text.rstrip(" ,.:")
            if not text:
                continue
            out.append({**cand, "text": text, "start": cand["start"] + block.start,
                        "end": cand["start"] + len(text) + block.start, "source": "model", "trusted": False})
    return out


def extract_fields(raw_document: str, reader: PIIReader) -> Extraction:
    """Run both readers over one KYC file and merge them into schema-checked fields."""
    text = fix_digit_noise(raw_document)
    header, blocks = split_document(text)
    regex = regex_candidates(text, blocks)
    model = _model_candidates(blocks, reader)
    kept, rejected = validate_and_arbitrate(regex + model)

    documents = [fields_for_block(block, kept) for block in blocks]
    fields: dict[str, Any] = {}
    for name in DOCUMENT_FIELDS:
        for document in documents:
            if name in document:
                fields[name] = document[name]
                break
    doc_type = doc_type_of(blocks)
    if doc_type:
        fields["doc_type"] = doc_type
    fields["form60"] = has_form60(blocks)
    risk = header.get("risk_category", "").lower()
    if risk in ("low", "medium", "high"):
        fields["risk_category"] = risk
    last_kyc = normalise_date(header.get("last_kyc_date", ""))
    if last_kyc:
        fields["last_kyc_date"] = last_kyc

    checked = ExtractedFields(**fields)
    stats = {
        "customer_id": header.get("customer_id"),
        "regex_candidates": len(regex),
        "model_candidates": len(model),
        "kept": len(kept),
        "rejected": len(rejected),
    }
    logger.info("extract: %d documents %s, fields %s", len(blocks), [b.kind for b in blocks], sorted(checked.as_state_fields()))
    return Extraction(checked.as_state_fields(), documents, stats)
