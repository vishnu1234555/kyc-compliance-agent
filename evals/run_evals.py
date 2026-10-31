"""Run every case in evals/cases.json through the KYC graph and print a scorecard.

Run: .venv/bin/python evals/run_evals.py   (set LOG_LEVEL=INFO for one log line per case)
The rules behind the expected labels are in evals/POLICY.md.

Extraction is scored on extract's own output, taken from the graph stream before validate
masks the Aadhaar number, and a second time with the PII model switched off (regex only), so
the per-field table shows what the model adds. Validate is scored twice as well: on the
expected fields (1.0 by construction) and on the extracted fields.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from kyc_agent import nodes
from kyc_agent.config import get_settings
from kyc_agent.extraction import extract_fields
from kyc_agent.graph import build_graph, run_config
from kyc_agent.nodes import validate
from kyc_agent.pii_model import NullReader, default_reader
from kyc_agent.state import KYCState
from kyc_agent.tracing import langsmith_client

logger = logging.getLogger("evals")

EVALS_DIR = Path(__file__).resolve().parent
CASES_PATH = EVALS_DIR / "cases.json"
LAST_RUN_PATH = EVALS_DIR / "last_run.json"

FIELDS = ("name", "dob", "pan", "form60", "aadhaar", "address", "pincode", "doc_type", "risk_category", "last_kyc_date")
Score = tuple[int, int]  # (hits, total)


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def fresh_state(raw_document: str) -> KYCState:
    """A KYCState with only raw_document filled, exactly what the graph gets at START."""
    return {
        "raw_document": raw_document,
        "extracted_fields": {},
        "documents": [],
        "validation_errors": [],
        "findings": [],
        "retrieved_rules": [],
        "rbi_citations": [],
        "decision": "",
        "reason": "",
        "rules_verdict": {},
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


def score_each_field(expected: dict[str, Any], got: dict[str, Any]) -> dict[str, Score]:
    """One (hit, 1) per non-null expected field."""
    scores: dict[str, Score] = {}
    for key, value in drop_nulls(expected).items():
        scores[key] = (1 if key in got and got[key] == value else 0, 1)
    return scores


def score_fields(expected: dict[str, Any], got: dict[str, Any]) -> Score:
    each = score_each_field(expected, got)
    return (sum(hit for hit, _ in each.values()), len(each))


def score_validate_on(fields: dict[str, Any], expected_errors: list[str]) -> Score:
    """Does the validate node, run on `fields`, produce exactly `expected_errors`?"""
    state = fresh_state("")
    state["extracted_fields"] = drop_nulls(fields)
    got = validate(state)["validation_errors"]
    return (1 if got == expected_errors else 0, 1)


def score_validate(expected_fields: dict[str, Any], expected_errors: list[str]) -> Score:
    return score_validate_on(expected_fields, expected_errors)


def select_cases(cases: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """EVAL_CASES="case-01,case-15" keeps only those; empty keeps all."""
    ids = {case_id.strip() for case_id in wanted.split(",") if case_id.strip()}
    return [case for case in cases if case["id"] in ids] if ids else cases


AUTO_REVIEW = {"reviewer": "eval-auto", "action": "confirm", "auto": True}


def run_graph(graph: CompiledStateGraph, raw_document: str, config: dict[str, Any] | None = None) -> tuple[dict[str, Any], KYCState]:
    """(extract's own output, before validate masks it; the final state).

    There is no human in an eval run: when the graph pauses at human_review, it is resumed at
    once with a confirm from "eval-auto", and the human_review audit entry says so (auto: True).
    """
    config = {**run_config(), **(config or {})}
    extracted: dict[str, Any] = {}
    final: KYCState | None = None
    graph_input: Any = fresh_state(raw_document)
    for _ in range(2):  # at most one pause per file
        interrupted = False
        for mode, chunk in graph.stream(graph_input, config=config, stream_mode=["updates", "values"]):
            if mode == "updates" and "extract" in chunk:
                extracted = chunk["extract"].get("extracted_fields", {})
            elif mode == "updates" and "__interrupt__" in chunk:
                interrupted = True
            elif mode == "values":
                final = chunk
        if not interrupted:
            break
        graph_input = Command(resume=AUTO_REVIEW)
    if final is None:
        raise RuntimeError("the graph produced no state")
    return extracted, final


def run_on_gold_fields(case: dict[str, Any]) -> tuple[dict[str, Any], KYCState]:
    """Decide on the expected fields instead of the extracted ones, to separate extraction errors
    from decision errors. extract still runs (for its per-document values and the extraction score);
    validate, retrieve_rules and decide then see the gold fields. human_review is not involved."""
    after_extract = nodes.extract(fresh_state(case["raw_document"]))
    extracted = dict(after_extract["extracted_fields"])
    gold_state: KYCState = {**after_extract, "extracted_fields": drop_nulls(case["expected_fields"])}
    final = nodes.decide(nodes.retrieve_rules(nodes.validate(gold_state)))
    return extracted, final


def run_case(graph: CompiledStateGraph, case: dict[str, Any], gold_fields: bool = False) -> dict[str, Any]:
    extracted, final = run_on_gold_fields(case) if gold_fields else run_graph(graph, case["raw_document"])
    regex_only = extract_fields(case["raw_document"], NullReader()).fields
    logger.info("%s: decision expected %r, got %r", case["id"], case["expected_decision"], final["decision"])
    baseline = final.get("rules_verdict", {}).get("decision", "")
    decide_entry = next((entry for entry in final["audit_log"] if entry.get("node") == "decide"), {})
    return {
        "id": case["id"],
        "tags": case.get("tags", []),
        "expected_decision": case["expected_decision"],
        "got_decision": final["decision"],
        "decision": score_decision(case["expected_decision"], final["decision"]),
        "decision_rules": score_decision(case["expected_decision"], baseline),
        "llm_error": decide_entry.get("llm_error", ""),
        "tool_calls": len([call for call in decide_entry.get("tool_calls", []) if call.get("tool") != "submit_verdict"]),
        "llm_calls": decide_entry.get("llm_calls", 0),
        "auto_reviewed": any(entry.get("node") == "human_review" and entry.get("auto") for entry in final["audit_log"]),
        "citations": score_citations(case["expected_citations"], final["rbi_citations"]),
        "fields": score_fields(case["expected_fields"], extracted),
        "fields_regex": score_fields(case["expected_fields"], regex_only),
        "each_field": score_each_field(case["expected_fields"], extracted),
        "each_field_regex": score_each_field(case["expected_fields"], regex_only),
        "validate": score_validate(case["expected_fields"], case["expected_errors"]),
        "validate_extracted": score_validate_on(extracted, case["expected_errors"]),
    }


def _add(total: Score, part: Score) -> Score:
    return (total[0] + part[0], total[1] + part[1])


def per_field_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Score]]]:
    """field -> reader ("model" | "regex") -> split ("all" | "labelled" | "unlabelled") -> (hits, total)."""
    table: dict[str, dict[str, dict[str, Score]]] = {}
    for field in FIELDS:
        table[field] = {reader: {split: (0, 0) for split in ("all", "labelled", "unlabelled")} for reader in ("model", "regex")}
    for row in rows:
        split = "unlabelled" if "unlabelled" in row.get("tags", []) else "labelled"
        for reader, key in (("model", "each_field"), ("regex", "each_field_regex")):
            for field, score in row.get(key, {}).items():
                table[field][reader]["all"] = _add(table[field][reader]["all"], score)
                table[field][reader][split] = _add(table[field][reader][split], score)
    return table


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_hits = 0
    rules_hits = 0
    llm_errors = 0
    tool_calls = 0
    llm_calls = 0
    auto_reviewed = 0
    citation_rates: list[float] = []
    fields: Score = (0, 0)
    fields_regex: Score = (0, 0)
    validate_hits = 0
    validate_extracted_hits = 0
    unlabelled = 0
    for row in rows:
        decision_hits += row["decision"][0]
        rules_hits += row.get("decision_rules", (0, 1))[0]
        llm_errors += 1 if row.get("llm_error") else 0
        tool_calls += row.get("tool_calls", 0)
        llm_calls += row.get("llm_calls", 0)
        auto_reviewed += 1 if row.get("auto_reviewed") else 0
        hits, total = row["citations"]
        if total > 0:
            citation_rates.append(hits / total)
        fields = _add(fields, row["fields"])
        fields_regex = _add(fields_regex, row.get("fields_regex", (0, 0)))
        validate_hits += row["validate"][0]
        validate_extracted_hits += row.get("validate_extracted", (0, 1))[0]
        if "unlabelled" in row.get("tags", []):
            unlabelled += 1
    return {
        "cases": len(rows),
        "decision_accuracy": safe_divide(decision_hits, len(rows)),
        "decision_hits": decision_hits,
        "decision_rules_accuracy": safe_divide(rules_hits, len(rows)),
        "decision_rules_hits": rules_hits,
        "llm_errors": llm_errors,
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "auto_reviewed": auto_reviewed,
        "citation_hit_rate": safe_divide(sum(citation_rates), len(citation_rates)),
        "citation_cases": len(citation_rates),
        "extraction_accuracy": safe_divide(*fields),
        "extraction_hits": fields[0],
        "extraction_total": fields[1],
        "extraction_regex_accuracy": safe_divide(*fields_regex),
        "extraction_regex_hits": fields_regex[0],
        "extraction_regex_total": fields_regex[1],
        "validate_accuracy": safe_divide(validate_hits, len(rows)),
        "validate_hits": validate_hits,
        "validate_extracted_accuracy": safe_divide(validate_extracted_hits, len(rows)),
        "validate_extracted_hits": validate_extracted_hits,
        "labelled_cases": len(rows) - unlabelled,
        "unlabelled_cases": unlabelled,
        "per_field": per_field_table(rows),
    }


def has_miss(row: dict[str, Any]) -> bool:
    for key in ("decision", "citations", "fields", "validate", "validate_extracted"):
        hits, total = row.get(key, (0, 0))
        if hits < total:
            return True
    return False


def _ratio(score: Score) -> str:
    return f"{score[0]}/{score[1]}"


def print_scorecard(summary: dict[str, Any], rows: list[dict[str, Any]], reader: str, decider: str) -> None:
    n = summary["cases"]
    print(f"KYC EVALS - {n} cases (PII model: {reader}; decide: {decider})")
    errors = f", {summary['llm_errors']} LLM errors fell back to the rules" if summary.get("llm_errors") else ""
    print(f"  decision accuracy       {summary['decision_hits']}/{n} = {summary['decision_accuracy']:.4f} (decide node{errors})")
    print(f"  decision, rules only    {summary['decision_rules_hits']}/{n} = {summary['decision_rules_accuracy']:.4f} (plain-code baseline, R1-R8 except R6)")
    fallbacks = [row["id"] for row in rows if row.get("llm_error")]
    if fallbacks:
        print(f"  LLM fallbacks           {', '.join(fallbacks)}")
    if summary.get("tool_calls"):
        print(f"  agent tool calls        {summary['tool_calls']} in total, {summary['tool_calls'] / n:.1f} per case; {summary['llm_calls']} LLM calls")
    if "auto_reviewed" in summary:
        print(f"  human_review            paused and auto-resumed by eval-auto in {summary['auto_reviewed']}/{n} cases (no human in an eval run)")
    print(f"  citation hit rate       {summary['citation_hit_rate']:.4f} (mean over {summary['citation_cases']} cases with expected citations)")
    print(f"  extraction accuracy     {summary['extraction_hits']}/{summary['extraction_total']} = {summary['extraction_accuracy']:.4f} (regex + PII model, extract's output before masking)")
    print(f"  extraction, regex only  {summary['extraction_regex_hits']}/{summary['extraction_regex_total']} = {summary['extraction_regex_accuracy']:.4f}")
    print(f"  validate accuracy       {summary['validate_hits']}/{n} = {summary['validate_accuracy']:.4f} (on expected fields; 1.0 by construction)")
    print(f"  validate on extracted   {summary['validate_extracted_hits']}/{n} = {summary['validate_extracted_accuracy']:.4f} (validate(extract output) == expected_errors)")
    print(f"\nPer field, hits/total. lab = {summary['labelled_cases']} cases with every value labelled, unlab = {summary['unlabelled_cases']} cases tagged unlabelled.")
    print(f"  {'field':<14} {'regex+model':<26} {'regex only':<26}")
    print(f"  {'':<14} {'all':<8} {'lab':<8} {'unlab':<8}  {'all':<8} {'lab':<8} {'unlab':<8}")
    for field, readers in summary["per_field"].items():
        model, regex = readers["model"], readers["regex"]
        print(f"  {field:<14} {_ratio(model['all']):<8} {_ratio(model['labelled']):<8} {_ratio(model['unlabelled']):<8}  "
              f"{_ratio(regex['all']):<8} {_ratio(regex['labelled']):<8} {_ratio(regex['unlabelled']):<8}")
    misses = [row for row in rows if has_miss(row)]
    print(f"\nMisses: {len(misses)} of {n} cases")
    if misses:
        print(f"  {'case':<8} {'decision expected -> got':<28} {'citations':<10} {'fields':<8} {'regex':<8} {'validate':<9} val(extr)")
    for row in misses:
        decision = f"{row['expected_decision']} -> {row['got_decision']!r}"
        citations = "-" if row["citations"][1] == 0 else _ratio(row["citations"])
        validate_ok = "ok" if row["validate"][0] == 1 else "MISS"
        extracted_ok = "ok" if row.get("validate_extracted", (1, 1))[0] == 1 else "MISS"
        print(f"  {row['id']:<8} {decision:<28} {citations:<10} {_ratio(row['fields']):<8} {_ratio(row.get('fields_regex', (0, 0))):<8} {validate_ok:<9} {extracted_ok}")


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    decider = settings.decide_mode if settings.decide_mode == "rules" else f"{settings.decide_mode} {settings.gemini_model}"
    gold_fields = os.environ.get("EVAL_GOLD_FIELDS", "") not in ("", "0", "false")
    if gold_fields:
        decider += " on GOLD fields"
    cases = select_cases(load_cases(), os.environ.get("EVAL_CASES", ""))
    graph = build_graph()
    rows = [run_case(graph, case, gold_fields) for case in cases]
    summary = summarise(rows)
    reader = default_reader().describe()  # after the run, so the revision and load time are known
    print_scorecard(summary, rows, reader, decider)
    LAST_RUN_PATH.write_text(json.dumps({"pii_model": reader, "decide": decider, **summary}, indent=2) + "\n", encoding="utf-8")
    if langsmith_client() is not None:
        langsmith_client().flush()
        print(f"  traces: LangSmith project {settings.langsmith_project} (12-digit numbers masked by the anonymizer)")


if __name__ == "__main__":
    main()
