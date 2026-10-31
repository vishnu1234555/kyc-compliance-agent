import re
from types import SimpleNamespace

from evals.langsmith_dataset import (
    citation_hit_rate,
    decision_match,
    examples_from_cases,
    extraction_accuracy,
    labels_for_masked_input,
)
from evals.run_evals import load_cases

CASES = load_cases()
BY_ID = {case["id"]: case for case in CASES}


def test_no_full_aadhaar_number_is_uploaded() -> None:
    inputs, outputs = examples_from_cases(CASES)
    assert len(inputs) == len(outputs) == 30
    twelve_digits = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
    assert not any(twelve_digits.search(example["raw_document"]) for example in inputs)
    assert not any(twelve_digits.search(str(example["expected_fields"].get("aadhaar"))) for example in outputs)
    assert sum(example["input_was_masked_for_upload"] for example in outputs) == 4  # cases 14, 20, 21, 30
    assert set(inputs[0]) == {"case_id", "raw_document"}


def test_masked_labels_drop_only_r3() -> None:
    assert labels_for_masked_input(BY_ID["case-14"])["expected_decision"] == "approve"
    twenty = labels_for_masked_input(BY_ID["case-20"])
    assert twenty["expected_decision"] == "re_kyc" and twenty["expected_citations"] == ["para 5(1)(xiv)", "para 23(3)"]
    assert twenty["expected_errors"] == [] and twenty["expected_fields"]["aadhaar"] == "XXXX-XXXX-1122"
    thirty = labels_for_masked_input(BY_ID["case-30"])
    assert thirty["expected_decision"] == "escalate" and thirty["expected_citations"] == ["para 17(2)"]
    unchanged = labels_for_masked_input(BY_ID["case-24"])
    assert unchanged["expected_decision"] == "escalate" and unchanged["expected_fields"] == BY_ID["case-24"]["expected_fields"]


def test_evaluators_score_like_run_evals() -> None:
    example = SimpleNamespace(outputs={"expected_decision": "re_kyc", "expected_citations": ["para 42(1)", "para 17(1)"],
                                       "expected_fields": {"pan": "ABCPE1234F", "dob": None, "pincode": "560001"}})
    run = SimpleNamespace(outputs={"decision": "re_kyc", "rbi_citations": ["para 42(1)"],
                                   "extracted_fields_before_masking": {"pan": "ABCPE1234F"}})
    assert decision_match(run, example) == {"key": "decision_match", "score": 1}
    assert citation_hit_rate(run, example) == {"key": "citation_hit_rate", "score": 0.5}
    assert extraction_accuracy(run, example) == {"key": "extraction_accuracy", "score": 0.5}
    no_citations = SimpleNamespace(outputs={**example.outputs, "expected_citations": []})
    assert citation_hit_rate(run, no_citations)["score"] is None  # left out of the mean, as in run_evals
