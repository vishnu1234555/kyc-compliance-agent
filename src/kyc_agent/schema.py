"""The contract for what extract puts into KYCState.extracted_fields (evals/POLICY.md section 4)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocType = Literal[
    "passport",
    "driving_licence",
    "aadhaar",
    "voter_id",
    "nrega_job_card",
    "npr_letter",
    "utility_bill",
    "property_tax_receipt",
    "pension_order",
    "allotment_letter",
]
RiskCategory = Literal["low", "medium", "high"]

_DATE = r"^\d{2}-\d{2}-\d{4}$"
# As printed: masked (XXXX-XXXX-1234) or in full (9999-1234-5678). extract never masks;
# validate does, after it has recorded that the number was visible.
_AADHAAR = r"^(?:XXXX|\d{4})-(?:XXXX|\d{4})-\d{4}$"


class ExtractedFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = None
    dob: str | None = Field(default=None, pattern=_DATE)
    pan: str | None = None  # kept as printed, even when malformed, so validate can flag it
    form60: bool = False
    aadhaar: str | None = Field(default=None, pattern=_AADHAAR)
    address: str | None = None
    pincode: str | None = None  # kept as printed, even when malformed
    doc_type: DocType | None = None
    risk_category: RiskCategory | None = None
    last_kyc_date: str | None = Field(default=None, pattern=_DATE)

    def as_state_fields(self) -> dict[str, Any]:
        """A missing field is left out, never sent as None: validate treats None as a wrong value."""
        return self.model_dump(exclude_none=True)
