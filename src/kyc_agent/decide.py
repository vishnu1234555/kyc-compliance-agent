"""The decision: a plain-code baseline (rules_decide) and the LLM verdict (llm_decide).

rules_decide applies evals/POLICY.md R1-R8 except R6 to the findings. It is the baseline the
LLM is measured against, and the fallback when the LLM cannot be reached.

llm_decide gives a Gemini Flash model, at temperature 0 and with structured output, the masked
fields, the validation errors and the retrieved RBI paragraphs, and asks for a decision, the
citations and a one-line reason. It may only cite paragraphs that were retrieved: anything
else is dropped in code, not trusted to the prompt.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from kyc_agent.backoff import with_backoff
from kyc_agent.findings import build_findings

logger = logging.getLogger(__name__)

SEVERITY = {"approve": 0, "re_kyc": 1, "escalate": 2}

# evals/POLICY.md section 3: rule -> (decision, citations). R6 is judgment, so rules_decide skips it.
RULE_TABLE: dict[str, tuple[str, list[str]]] = {
    "R1": ("re_kyc", ["para 5(1)(xiv)", "para 23(3)"]),
    "R2": ("re_kyc", ["para 23(5)"]),
    "R3": ("re_kyc", ["para 23 Explanation 1"]),
    "R4": ("re_kyc", ["para 42(1)"]),
    "R5": ("re_kyc", []),
    "R6": ("escalate", ["para 17(1)"]),
    "R7": ("escalate", ["para 17(2)"]),
    "R8": ("escalate", []),
}

SYSTEM_PROMPT = """You are the compliance reviewer of an Indian NBFC doing the periodic KYC review of ONE existing customer's file under the Reserve Bank of India (Non-Banking Financial Companies - Know Your Customer) Directions, 2025.

Decide exactly one of: approve, re_kyc, escalate. The most serious outcome wins (escalate > re_kyc > approve).

Decision policy. Apply every rule that fits, in this order:
1. re_kyc - no officially valid document (OVD) on file: the identity document (doc_type) is missing or is address-only (utility_bill, property_tax_receipt, pension_order, allotment_letter). OVDs are passport, driving_licence, aadhaar, voter_id, nrega_job_card, npr_letter. Cite para 5(1)(xiv) and para 23(3).
2. re_kyc - no PAN and no Form 60 (form60 false), or the PAN is in the wrong format (validation error "PAN format invalid"). Cite para 23(5).
3. re_kyc - an Aadhaar number was visible in full in the file: the validation error "Aadhaar must be masked as XXXX-XXXX-1234" means it was (the value you see is already masked). Cite para 23 Explanation 1.
4. re_kyc - periodic updation overdue on the as-of date: last_kyc_date plus 2 years (high risk), 8 years (medium) or 10 years (low) is before the as-of date. Cite para 42(1).
5. re_kyc - PIN code invalid (validation error "PIN code invalid"). No citation.
6. escalate - the name is anonymous, fictitious, benami or a placeholder (for example Test Customer, XYZ, Demo User). Cite para 17(1).
7. escalate - customer due diligence cannot be completed: the name or date of birth differs between documents in the file, or the customer refused to give documents. Cite para 17(2).
8. escalate - the customer is under 18 (validation error "Date of birth invalid or applicant under 18"). No citation.
Nothing applies - approve, no citations. Aadhaar is not mandatory: a file with no Aadhaar at all is fine (para 23 Explanation 4).

Citations: only paragraph references that appear in the RETRIEVED RULES, written exactly as given there (for example "para 42(1)"), in the order of the rules above, without duplicates. If a rule fires but its paragraph was not retrieved, still apply the rule and leave the citation out.
Reason: one line, plain English, naming the rules that fired or saying nothing fired."""


class Verdict(BaseModel):
    """What the LLM must return. Pydantic enforces the shape; the code enforces the citations."""

    decision: Literal["approve", "re_kyc", "escalate"]
    citations: list[str] = Field(default_factory=list, description='paragraph references exactly as retrieved, e.g. "para 42(1)"')
    reason: str = Field(description="one line: which rules fired, or that nothing fired")


@dataclass
class Decision:
    decision: str
    citations: list[str]
    reason: str
    rules: list[str] = field(default_factory=list)
    dropped_citations: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "citations": self.citations, "reason": self.reason, "rules": self.rules}


def rules_decide(state: dict[str, Any], as_of: date | None = None) -> Decision:
    """POLICY.md R1-R8 except R6, applied to the findings. Deterministic; the baseline."""
    findings = state.get("findings") or [finding.as_dict() for finding in build_findings(state, as_of)]
    decision = "approve"
    citations: list[str] = []
    fired: list[str] = []
    for finding in findings:
        if finding.get("tentative"):
            continue  # R6 needs judgment; the baseline does not guess
        rule = finding["rule"]
        outcome, rule_citations = RULE_TABLE[rule]
        if SEVERITY[outcome] > SEVERITY[decision]:
            decision = outcome
        for citation in rule_citations:
            if citation not in citations:
                citations.append(citation)
        if rule not in fired:
            fired.append(rule)
    texts = [finding["text"] for finding in findings if not finding.get("tentative")]
    reason = "; ".join(texts) if texts else "Nothing fires: OVD on file, PAN or Form 60 present, Aadhaar masked, KYC up to date"
    return Decision(decision, citations, reason, rules=fired)


def build_messages(state: dict[str, Any], as_of: date | None = None) -> list[tuple[str, str]]:
    """The prompt: masked fields, validation errors, retrieved paragraphs. Never the raw document."""
    today = (as_of or date.today()).strftime("%d-%m-%Y")
    retrieved = state.get("retrieved_rules", [])
    rules_text = "\n\n".join(f"[{chunk['citation']}] {chunk['text']}" for chunk in retrieved) or "(nothing retrieved)"
    human = (
        f"As-of date: {today}\n\n"
        f"Extracted fields (Aadhaar already masked):\n{json.dumps(state.get('extracted_fields', {}), indent=2)}\n\n"
        f"Validation errors: {json.dumps(state.get('validation_errors', []))}\n\n"
        f"RETRIEVED RULES (the only paragraphs you may cite):\n{rules_text}"
    )
    return [("system", SYSTEM_PROMPT), ("human", human)]


def keep_retrieved_citations(citations: list[str], state: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Citations the LLM may make are exactly the retrieved ones; the rest are dropped, in code."""
    allowed = {chunk["citation"] for chunk in state.get("retrieved_rules", [])}
    kept: list[str] = []
    dropped: list[str] = []
    for citation in citations:
        if citation in allowed and citation not in kept:
            kept.append(citation)
        elif citation not in allowed:
            dropped.append(citation)
    return kept, dropped


def llm_decide(state: dict[str, Any], model: Any, as_of: date | None = None) -> Decision:
    """One structured-output call, retried on 429/503. Raises after the retry schedule is exhausted."""
    structured = model.with_structured_output(Verdict)
    messages = build_messages(state, as_of)
    verdict: Verdict = with_backoff(lambda: structured.invoke(messages), what="decide LLM call")
    kept, dropped = keep_retrieved_citations(list(verdict.citations), state)
    if dropped:
        logger.warning("decide: dropped citations not among retrieved rules: %s", dropped)
    return Decision(verdict.decision, kept, " ".join(verdict.reason.split()), dropped_citations=dropped)
