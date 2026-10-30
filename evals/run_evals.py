"""Run every case in evals/cases.json through the KYC graph and print a scorecard.

Run: .venv/bin/python evals/run_evals.py   (set LOG_LEVEL=INFO for one log line per case)
The rules behind the expected labels are in evals/POLICY.md.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from kyc_agent.graph import build_graph
from kyc_agent.nodes import validate
from kyc_agent.state import KYCState

logger = logging.getLogger("evals")

EVALS_DIR = Path(__file__).resolve().parent
CASES_PATH = EVALS_DIR / "cases.json"
LAST_RUN_PATH = EVALS_DIR / "last_run.json"

Score = tuple[int, int]  # (hits, total)


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fresh_state(raw_document: str) -> KYCState:
    """A KYCState with only raw_document filled, exactly what the graph gets at START."""
    return {
        "raw_document": raw_document,
        "extracted_fields": {},
        "validation_errors": [],
        "rbi_citations": [],
        "decision": "",
        "audit_log": [],
    }


def drop_nulls(fields: dict[str, Any]) -> dict[str, Any]:
    """A missing field is not a wrong field, so nulls are never scored or validated."""
    return {key: value for key, value in fields.items() if value is not None}


def safe_divide(hits: float, total: float) -> float:
    return hits / total if total else 0.0


def score_decision(expected: str, got: str) -> Score:
    return (1 if got == expected else 0, 1)


def score_citations(expected: list[str], got: list[str]) -> Score:
    hits = 0
    for citation in expected:
        if citation in got:
            hits += 1
    return (hits, len(expected))


def score_fields(expected: dict[str, Any], got: dict[str, Any]) -> Score:
    wanted = drop_nulls(expected)
    hits = 0
    for key, value in wanted.items():
        if key in got and got[key] == value:
            hits += 1
    return (hits, len(wanted))


def score_validate(expected_fields: dict[str, Any], expected_errors: list[str]) -> Score:
    state = fresh_state("")
    state["extracted_fields"] = drop_nulls(expected_fields)
    got = validate(state)["validation_errors"]
    return (1 if got == expected_errors else 0, 1)


def run_case(graph: CompiledStateGraph, case: dict[str, Any]) -> dict[str, Any]:
    final = graph.invoke(fresh_state(case["raw_document"]))
    logger.info("%s: decision expected %r, got %r", case["id"], case["expected_decision"], final["decision"])
    return {
        "id": case["id"],
        "expected_decision": case["expected_decision"],
        "got_decision": final["decision"],
        "decision": score_decision(case["expected_decision"], final["decision"]),
        "citations": score_citations(case["expected_citations"], final["rbi_citations"]),
        "fields": score_fields(case["expected_fields"], final["extracted_fields"]),
        "validate": score_validate(case["expected_fields"], case["expected_errors"]),
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_hits = 0
    citation_rates: list[float] = []
    field_hits = 0
    field_total = 0
    validate_hits = 0
    for row in rows:
        decision_hits += row["decision"][0]
        hits, total = row["citations"]
        if total > 0:
            citation_rates.append(hits / total)
        field_hits += row["fields"][0]
        field_total += row["fields"][1]
        validate_hits += row["validate"][0]
    return {
        "cases": len(rows),
        "decision_accuracy": safe_divide(decision_hits, len(rows)),
        "decision_hits": decision_hits,
        "citation_hit_rate": safe_divide(sum(citation_rates), len(citation_rates)),
        "citation_cases": len(citation_rates),
        "extraction_accuracy": safe_divide(field_hits, field_total),
        "extraction_hits": field_hits,
        "extraction_total": field_total,
        "validate_accuracy": safe_divide(validate_hits, len(rows)),
        "validate_hits": validate_hits,
    }


def has_miss(row: dict[str, Any]) -> bool:
    for key in ("decision", "citations", "fields", "validate"):
        if row[key][0] < row[key][1]:
            return True
    return False


def print_scorecard(summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    n = summary["cases"]
    print(f"KYC EVALS - {n} cases")
    print(f"  decision accuracy    {summary['decision_hits']}/{n} = {summary['decision_accuracy']:.4f}")
    print(f"  citation hit rate    {summary['citation_hit_rate']:.4f} (mean over {summary['citation_cases']} cases with expected citations)")
    print(f"  extraction accuracy  {summary['extraction_hits']}/{summary['extraction_total']} = {summary['extraction_accuracy']:.4f}")
    print(f"  validate accuracy    {summary['validate_hits']}/{n} = {summary['validate_accuracy']:.4f}")
    misses = [row for row in rows if has_miss(row)]
    print(f"\nMisses: {len(misses)} of {n} cases")
    if misses:
        print(f"  {'case':<8} {'decision expected -> got':<28} {'citations':<10} {'fields':<8} validate")
    for row in misses:
        decision = f"{row['expected_decision']} -> {row['got_decision']!r}"
        citations = "-" if row["citations"][1] == 0 else f"{row['citations'][0]}/{row['citations'][1]}"
        fields = f"{row['fields'][0]}/{row['fields'][1]}"
        validate_ok = "ok" if row["validate"][0] == 1 else "MISS"
        print(f"  {row['id']:<8} {decision:<28} {citations:<10} {fields:<8} {validate_ok}")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    cases = load_cases()
    graph = build_graph()
    rows = [run_case(graph, case) for case in cases]
    summary = summarise(rows)
    print_scorecard(summary, rows)
    LAST_RUN_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
