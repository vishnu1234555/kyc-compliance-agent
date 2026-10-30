"""Split one KYC file into its record header and the documents on file.

Plain text rules only. A file looks like this (see evals/POLICY.md section 2):

    KYC RECORD - PERIODIC REVIEW
    Customer ID: NB-10001
    Risk category: Medium
    Last KYC date: 14-02-2022

    [Document 1: Passport]
    ...

    [Document 2: PAN card]
    ...

Documents are separated by blank lines. Most start with a [bracketed title]; when they do
not, the document kind is read from its first few lines ("INCOME TAX DEPARTMENT", "Unique
Identification Authority of India").
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

OVD_TYPES = ("passport", "driving_licence", "aadhaar", "voter_id", "nrega_job_card", "npr_letter")
ADDRESS_ONLY_TYPES = ("utility_bill", "property_tax_receipt", "pension_order", "allotment_letter")
IDENTITY_KINDS = OVD_TYPES + ADDRESS_ONLY_TYPES
OTHER_KINDS = ("pan_card", "form60", "note", "unknown")

_TITLE = re.compile(r"^\s*\[(.+?)\]\s*$")
_IS_HEADER = re.compile(r"KYC RECORD|customer\s*id\s*[:\-]", re.I)
_HEADER_FIELDS = {
    "customer_id": re.compile(r"^\s*customer\s*id\s*[:\-]\s*(.+?)\s*$", re.I | re.M),
    "risk_category": re.compile(r"^\s*risk\s*category\s*[:\-]\s*([A-Za-z]+)\s*$", re.I | re.M),
    "last_kyc_date": re.compile(
        r"^\s*last\s*kyc\s*(?:date|updat\w*)\s*[:\-]\s*(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})\s*$", re.I | re.M
    ),
}

# (kind, pattern): the first rule that matches the heading wins, so the specific forms
# (a branch note, a Form 60 that mentions "Permanent Account Number") sit above the
# generic ones.
_KIND_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("note", re.compile(r"branch note|file note|case note|note dated", re.I)),
    ("form60", re.compile(r"form\s*no\.?\s*60\b|form\s*60\b", re.I)),
    ("pan_card", re.compile(r"income tax department|permanent account number|\bpan (?:card|no\.?|number)\b", re.I)),
    ("passport", re.compile(r"passport", re.I)),
    ("driving_licence", re.compile(r"driving licen[cs]e|\bDL no\b", re.I)),
    ("voter_id", re.compile(r"voter|election commission|elector'?s|\bEPIC\b", re.I)),
    ("nrega_job_card", re.compile(r"\bM?G?NREGA\b|job card|employment guarantee", re.I)),
    ("npr_letter", re.compile(r"national population register|\bNPR\b", re.I)),
    ("property_tax_receipt", re.compile(r"property tax|municipal tax|house tax", re.I)),
    ("pension_order", re.compile(r"pension payment order|\bPPO\b|pension", re.I)),
    ("allotment_letter", re.compile(r"allotment", re.I)),
    ("utility_bill", re.compile(
        r"electricity|utility bill|telephone bill|mobile bill|piped gas|gas bill|water bill"
        r"|consumer no|generation and distribution|power corporation|electricity board|\bbill date\b", re.I)),
    ("aadhaar", re.compile(r"aadha?ar|unique identification authority|\bUIDAI\b|\bX{4}[\s-]?X{4}[\s-]?\d{4}\b", re.I)),
]


@dataclass(frozen=True)
class Block:
    """One document in the file, with its offset in the text it was cut from."""

    index: int
    start: int
    text: str
    title: str | None
    kind: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    @property
    def body(self) -> str:
        """The document without its [title] line."""
        if self.title is None:
            return self.text
        return self.text.split("\n", 1)[1] if "\n" in self.text else ""

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


def parse_header(header_text: str) -> dict[str, str]:
    """Record-header fields as printed (dates are normalised later by the reader)."""
    found: dict[str, str] = {}
    for key, pattern in _HEADER_FIELDS.items():
        match = pattern.search(header_text)
        if match:
            found[key] = match.group(1).strip()
    return found


def classify(title: str | None, body: str) -> str:
    """Document kind from the title, else from the first three lines, else from the whole body."""
    heading_lines = [line for line in body.split("\n") if line.strip()][:3]
    for scope in ([title] if title else []) + ["\n".join(heading_lines), body]:
        for kind, pattern in _KIND_RULES:
            if pattern.search(scope):
                return kind
    return "unknown"


def split_document(text: str) -> tuple[dict[str, str], list[Block]]:
    """(header fields, document blocks). The header is the first paragraph when it looks like one."""
    header: dict[str, str] = {}
    blocks: list[Block] = []
    offset = 0
    for chunk in re.split(r"(\n[ \t]*\n+)", text):
        if chunk.strip():
            if not header and not blocks and _IS_HEADER.search(chunk):
                header = parse_header(chunk)
            else:
                first_line = chunk.lstrip("\n").split("\n", 1)[0]
                title_match = _TITLE.match(first_line)
                title = title_match.group(1).strip() if title_match else None
                body = chunk.split("\n", 1)[1] if title else chunk
                blocks.append(Block(len(blocks), offset, chunk, title, classify(title, body)))
        offset += len(chunk)
    logger.debug("split_document: header keys %s, %d blocks %s", sorted(header), len(blocks), [b.kind for b in blocks])
    return header, blocks


def doc_type_of(blocks: list[Block]) -> str | None:
    """The first identity or address document on file; PAN cards, Form 60 and notes do not count."""
    for block in blocks:
        if block.kind in IDENTITY_KINDS:
            return block.kind
    return None


def has_form60(blocks: list[Block]) -> bool:
    return any(block.kind == "form60" for block in blocks)
