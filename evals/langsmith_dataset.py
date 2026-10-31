"""Upload evals/cases.json as a LangSmith dataset and run the scorecard against it once.

Run: .venv/bin/python evals/langsmith_dataset.py [--upload-only]
Needs LANGSMITH_API_KEY. The graph runs with the same settings as run_evals.py (DECIDE_MODE,
GEMINI_MODEL, PII_MODEL); every trace goes through the anonymizer in kyc_agent.tracing.
The uploaded documents are masked (no full Aadhaar number leaves the machine), so the dataset's
labels are the policy applied to the masked file: four cases that fire R3 locally (14, 20, 21,
30) carry a different expected decision here, with the original kept under original_decision.
The experiment shows up in LangSmith under the dataset, one row per case, with the same three
per-case scores run_evals.py uses: decision match, citation hit rate, extraction accuracy.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so "evals" imports when run as a script

from evals.run_evals import load_cases, run_graph, safe_divide, score_citations, score_fields  # noqa: E402
from kyc_agent.config import get_settings  # noqa: E402
from kyc_agent.decide import RULE_TABLE  # noqa: E402
from kyc_agent.graph import build_graph  # noqa: E402
from kyc_agent.tracing import langsmith_client, mask_text  # noqa: E402

logger = logging.getLogger("evals.langsmith")

DATASET_NAME = "kyc-compliance-agent-cases"


SEVERITY = {"approve": 0, "re_kyc": 1, "escalate": 2}
AADHAAR_ERROR = "Aadhaar must be masked as XXXX-XXXX-1234"


def fired_rules(case: dict[str, Any]) -> list[str]:
    """'R2+R4: ...' -> ['R2', 'R4'];  'none: ...' -> []  (the same prefix the tests parse)."""
    prefix = case["why"].split(":", 1)[0]
    return [] if prefix == "none" else prefix.split("+")


def labels_for_masked_input(case: dict[str, Any]) -> dict[str, Any]:
    """The policy applied to the masked copy of the file: every rule but R3 can still fire."""
    decision = "approve"
    citations: list[str] = []
    for rule in fired_rules(case):
        if rule == "R3":
            continue
        outcome, rule_citations = RULE_TABLE[rule]
        if SEVERITY[outcome] > SEVERITY[decision]:
            decision = outcome
        for citation in rule_citations:
            if citation not in citations:
                citations.append(citation)
    fields = dict(case["expected_fields"])
    if fields.get("aadhaar"):
        fields["aadhaar"] = mask_text(fields["aadhaar"])
    return {
        "expected_decision": decision,
        "expected_citations": citations,
        "expected_fields": fields,
        "expected_errors": [error for error in case["expected_errors"] if error != AADHAAR_ERROR],
    }


def examples_from_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(inputs, outputs) per case. SAFETY: a full Aadhaar number never goes to LangSmith, so the
    uploaded document is masked and the labels are those of the masked file (R3 cannot fire on
    it); the original labels ride along under original_* for reference."""
    inputs = [{"case_id": case["id"], "raw_document": mask_text(case["raw_document"])} for case in cases]
    outputs = []
    for case in cases:
        masked = labels_for_masked_input(case)
        outputs.append({
            **masked,
            "original_decision": case["expected_decision"],
            "original_citations": case["expected_citations"],
            "input_was_masked_for_upload": mask_text(case["raw_document"]) != case["raw_document"],
            "tags": case.get("tags", []),
            "why": case["why"],
        })
    return inputs, outputs


def upload_dataset(client: Any, cases: list[dict[str, Any]], name: str = DATASET_NAME) -> Any:
    """Create the dataset once; later runs reuse it. Returns the dataset."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
        logger.info("dataset %s exists (%s); reusing", name, dataset.id)
        return dataset
    dataset = client.create_dataset(dataset_name=name, description="30 synthetic KYC files with labels from evals/POLICY.md")
    inputs, outputs = examples_from_cases(cases)
    client.create_examples(inputs=inputs, outputs=outputs, dataset_id=dataset.id)
    logger.info("dataset %s created with %d examples", name, len(inputs))
    return dataset


def decision_match(run: Any, example: Any) -> dict[str, Any]:
    got = (run.outputs or {}).get("decision", "")
    return {"key": "decision_match", "score": 1 if got == example.outputs["expected_decision"] else 0}


def citation_hit_rate(run: Any, example: Any) -> dict[str, Any]:
    hits, total = score_citations(example.outputs["expected_citations"], (run.outputs or {}).get("rbi_citations", []))
    return {"key": "citation_hit_rate", "score": safe_divide(hits, total) if total else None}


def extraction_accuracy(run: Any, example: Any) -> dict[str, Any]:
    hits, total = score_fields(example.outputs["expected_fields"], (run.outputs or {}).get("extracted_fields_before_masking", {}))
    return {"key": "extraction_accuracy", "score": safe_divide(hits, total)}


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    client = langsmith_client()
    if client is None:
        raise SystemExit("LANGSMITH_API_KEY is not set; nothing to upload")
    cases = load_cases()
    dataset = upload_dataset(client, cases)
    if "--upload-only" in sys.argv:
        print(f"dataset {dataset.name} ready: {len(cases)} cases")
        return

    from langsmith import evaluate

    settings = get_settings()
    graph = build_graph()

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        extracted, final = run_graph(graph, inputs["raw_document"])  # run_config() attaches the anonymising tracer
        return {
            "decision": final["decision"],
            "rbi_citations": final["rbi_citations"],
            "reason": final.get("reason", ""),
            "extracted_fields_before_masking": extracted,
            "audit_log": final["audit_log"],
        }

    results = evaluate(
        target,
        data=dataset.name,
        evaluators=[decision_match, citation_hit_rate, extraction_accuracy],
        experiment_prefix=f"kyc-{settings.decide_mode}",
        metadata={"decide_mode": settings.decide_mode, "gemini_model": settings.gemini_model, "pii_model": settings.pii_model},
        max_concurrency=1,
        client=client,
    )
    client.flush()
    print(f"experiment {results.experiment_name}: {len(cases)} cases evaluated against dataset {dataset.name}")


if __name__ == "__main__":
    main()
