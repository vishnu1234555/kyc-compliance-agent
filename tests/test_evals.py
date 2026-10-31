"""Tests for evals/: the scoring helpers and the case file itself.

The case-file tests pin the rule table from evals/POLICY.md, so a case whose label
drifts from the table fails here before it can mislead a scorecard.
"""

import re

import pytest

from evals.run_evals import (
    drop_nulls,
    load_cases,
    score_citations,
    score_decision,
    score_each_field,
    score_fields,
    score_validate,
    score_validate_on,
    summarise,
)

CASES = load_cases()
CASE_IDS = [case["id"] for case in CASES]

CASE_KEYS = {"id", "raw_document", "expected_fields", "expected_errors",
             "expected_decision", "expected_citations", "why"}
OPTIONAL_KEYS = {"tags"}
ALLOWED_TAGS = {"unlabelled", "ocr_noise", "no_titles", "unmasked_aadhaar"}
FIELD_KEYS = {"name", "dob", "pan", "form60", "aadhaar", "address", "pincode",
              "doc_type", "risk_category", "last_kyc_date"}
SEVERITY = {"approve": 0, "re_kyc": 1, "escalate": 2}
OVD_TYPES = {"passport", "driving_licence", "aadhaar", "voter_id", "nrega_job_card", "npr_letter"}
ADDRESS_ONLY_TYPES = {"utility_bill", "property_tax_receipt", "pension_order", "allotment_letter"}
RISK_CATEGORIES = {"low", "medium", "high"}

# Rule table from evals/POLICY.md: rule id -> (decision, citations). Keep the two in step.
RULES = {
    "R1": ("re_kyc", ["para 5(1)(xiv)", "para 23(3)"]),
    "R2": ("re_kyc", ["para 23(5)"]),
    "R3": ("re_kyc", ["para 23 Explanation 1"]),
    "R4": ("re_kyc", ["para 42(1)"]),
    "R5": ("re_kyc", []),
    "R6": ("escalate", ["para 17(1)"]),
    "R7": ("escalate", ["para 17(2)"]),
    "R8": ("escalate", []),
}
ALLOWED_CITATIONS = {"para 5(1)(xiv)", "para 23(3)", "para 23(5)", "para 23 Explanation 1",
                     "para 42(1)", "para 17(1)", "para 17(2)"}

DATE = re.compile(r"^[0-9]{2}-[0-9]{2}-[0-9]{4}$")
AADHAAR = re.compile(r"^(XXXX-XXXX-[0-9]{4}|9999-[0-9]{4}-[0-9]{4})$")


def fired_rules(case: dict) -> list[str]:
    """'R2+R4: ...' -> ['R2', 'R4'];  'none: ...' -> []."""
    prefix = case["why"].split(":", 1)[0]
    if prefix == "none":
        return []
    return prefix.split("+")


# --- scoring helpers on tiny hand-made inputs ---------------------------------

def test_score_decision() -> None:
    assert score_decision("approve", "approve") == (1, 1)
    assert score_decision("approve", "") == (0, 1)


def test_score_citations_counts_each_expected_citation() -> None:
    assert score_citations(["para 42(1)", "para 17(1)"], ["para 17(1)"]) == (1, 2)
    assert score_citations(["para 42(1)"], ["para 42(1)", "para 99"]) == (1, 1)
    assert score_citations([], ["para 42(1)"]) == (0, 0)


def test_score_fields_ignores_nulls_and_needs_exact_match() -> None:
    expected = {"pan": "ABCPE1234F", "dob": None, "pincode": "560001"}
    assert score_fields(expected, {"pan": "ABCPE1234F", "pincode": "560001"}) == (2, 2)
    assert score_fields(expected, {"pan": "abcpe1234f", "dob": "x"}) == (0, 2)
    assert score_fields(expected, {}) == (0, 2)


def test_score_validate_runs_the_real_validate_node() -> None:
    fields = {"pan": "bad", "aadhaar": None, "pincode": "560001"}
    assert score_validate(fields, ["PAN format invalid"]) == (1, 1)
    assert score_validate(fields, []) == (0, 1)
    assert score_validate_on({"pan": "ABCPE1234F"}, []) == (1, 1)


def test_score_each_field_scores_only_non_null_expected_fields() -> None:
    expected = {"pan": "ABCPE1234F", "dob": None, "pincode": "560001"}
    assert score_each_field(expected, {"pan": "ABCPE1234F"}) == {"pan": (1, 1), "pincode": (0, 1)}


def test_drop_nulls() -> None:
    assert drop_nulls({"a": 1, "b": None, "c": False}) == {"a": 1, "c": False}


def test_summarise_two_rows() -> None:
    rows = [
        {"decision": (1, 1), "citations": (1, 2), "fields": (3, 4), "fields_regex": (2, 4), "validate": (1, 1),
         "validate_extracted": (1, 1), "tags": ["unlabelled"],
         "each_field": {"name": (1, 1), "pan": (1, 1)}, "each_field_regex": {"name": (0, 1), "pan": (1, 1)}},
        {"decision": (0, 1), "citations": (0, 0), "fields": (0, 2), "fields_regex": (0, 2), "validate": (0, 1),
         "validate_extracted": (0, 1), "tags": [],
         "each_field": {"name": (0, 1)}, "each_field_regex": {"name": (0, 1)}},
    ]
    summary = summarise(rows)
    assert summary["cases"] == 2
    assert summary["decision_accuracy"] == 0.5
    assert summary["citation_hit_rate"] == 0.5  # only the first row has expected citations
    assert summary["citation_cases"] == 1
    assert summary["extraction_accuracy"] == 0.5  # 3 of 6 fields
    assert summary["extraction_regex_accuracy"] == 2 / 6
    assert summary["validate_accuracy"] == 0.5
    assert summary["validate_extracted_accuracy"] == 0.5
    assert summary["labelled_cases"] == 1 and summary["unlabelled_cases"] == 1
    assert summary["per_field"]["name"]["model"] == {"all": (1, 2), "labelled": (0, 1), "unlabelled": (1, 1)}
    assert summary["per_field"]["name"]["regex"]["all"] == (0, 2)
    assert summary["per_field"]["pan"]["model"]["unlabelled"] == (1, 1)


def test_summarise_empty_is_all_zero() -> None:
    summary = summarise([])
    assert summary["decision_accuracy"] == 0.0
    assert summary["citation_hit_rate"] == 0.0


# --- the case file as a whole -------------------------------------------------

def test_thirty_cases_with_unique_ids() -> None:
    assert len(CASES) == 30
    assert len(set(CASE_IDS)) == 30


def test_decision_mix() -> None:
    decisions = [case["expected_decision"] for case in CASES]
    assert decisions.count("approve") >= 8
    assert decisions.count("re_kyc") >= 11
    assert decisions.count("escalate") >= 8


def test_every_rule_fires_in_at_least_two_cases() -> None:
    for rule in RULES:
        firing = [case["id"] for case in CASES if rule in fired_rules(case)]
        assert len(firing) >= 2, f"{rule} fires only in {firing}"


def test_at_least_three_cases_fire_two_rules() -> None:
    multi = [case["id"] for case in CASES if len(fired_rules(case)) >= 2]
    assert len(multi) >= 3, multi


def test_all_six_ovd_types_and_the_special_files_are_covered() -> None:
    doc_types = {case["expected_fields"]["doc_type"] for case in CASES}
    assert OVD_TYPES <= doc_types
    assert any(case["expected_fields"]["form60"] for case in CASES)
    assert any(case["expected_fields"]["aadhaar"] is None for case in CASES)


# --- every case on its own ----------------------------------------------------

def test_tags_split_the_cases_without_hardcoding_ids() -> None:
    unlabelled = [case["id"] for case in CASES if "unlabelled" in case.get("tags", [])]
    assert 3 <= len(unlabelled) <= len(CASES) - 3
    assert any("ocr_noise" in case.get("tags", []) for case in CASES)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_case_has_the_required_keys_and_known_tags(case: dict) -> None:
    assert CASE_KEYS <= set(case) <= CASE_KEYS | OPTIONAL_KEYS
    assert set(case["expected_fields"]) == FIELD_KEYS
    tags = case.get("tags", [])
    assert isinstance(tags, list) and set(tags) <= ALLOWED_TAGS and len(tags) == len(set(tags))


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_decision_and_citations_are_allowed(case: dict) -> None:
    assert case["expected_decision"] in SEVERITY
    citations = case["expected_citations"]
    assert set(citations) <= ALLOWED_CITATIONS, citations
    assert len(citations) == len(set(citations)), "duplicate citation"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_label_follows_the_rule_table(case: dict) -> None:
    rules = fired_rules(case)
    expected_decision = "approve"
    expected_citations: list[str] = []
    for rule in rules:
        decision, citations = RULES[rule]
        if SEVERITY[decision] > SEVERITY[expected_decision]:
            expected_decision = decision
        for citation in citations:
            if citation not in expected_citations:
                expected_citations.append(citation)
    assert case["expected_decision"] == expected_decision
    assert case["expected_citations"] == expected_citations


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_expected_errors_match_the_validate_node(case: dict) -> None:
    assert score_validate(case["expected_fields"], case["expected_errors"]) == (1, 1)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_field_formats(case: dict) -> None:
    fields = case["expected_fields"]
    assert fields["name"] is None or fields["name"] == fields["name"].upper()
    assert fields["dob"] is None or DATE.fullmatch(fields["dob"])
    assert DATE.fullmatch(fields["last_kyc_date"])
    assert fields["pan"] is None or (fields["pan"] == fields["pan"].upper() and fields["pan"][3] == "P")
    assert isinstance(fields["form60"], bool)
    assert fields["aadhaar"] is None or AADHAAR.fullmatch(fields["aadhaar"])
    assert fields["pincode"] is None or isinstance(fields["pincode"], str)
    assert fields["doc_type"] is None or fields["doc_type"] in OVD_TYPES | ADDRESS_ONLY_TYPES
    assert fields["risk_category"] in RISK_CATEGORIES


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_record_header_is_in_the_raw_document(case: dict) -> None:
    fields = case["expected_fields"]
    assert f"Last KYC date: {fields['last_kyc_date']}" in case["raw_document"]
    assert f"Risk category: {fields['risk_category'].capitalize()}" in case["raw_document"]


def test_select_cases_and_gold_fields_pipeline() -> None:
    from evals.run_evals import run_on_gold_fields, select_cases

    assert [case["id"] for case in select_cases(CASES, "case-24, case-01")] == ["case-01", "case-24"]
    assert select_cases(CASES, "") is CASES
    case = next(case for case in CASES if case["id"] == "case-26")  # refusal note: R7 without the PII model
    extracted, final = run_on_gold_fields(case)  # DECIDE_MODE=rules under the test fixtures
    assert extracted["name"] == "DEVENDRA RAWAT" and "dob" not in extracted
    assert final["extracted_fields"] == drop_nulls(case["expected_fields"])
    assert final["rules_verdict"]["decision"] == "escalate" and final["rules_verdict"]["rules"] == ["R1", "R2", "R7"]
    assert [entry["node"] for entry in final["audit_log"]] == ["extract", "validate", "retrieve_rules", "decide"]
