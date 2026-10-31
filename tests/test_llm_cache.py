import json
from pathlib import Path
from typing import Any

import pytest

from evals import run_evals
from evals.run_evals import cache_dir_for, is_daily_quota, load_cases, mode_name, resume_command, run_all, run_case, summarise
from kyc_agent import nodes
from kyc_agent.decide import Verdict
from kyc_agent.graph import build_graph

CASE_01 = next(case for case in load_cases() if case["id"] == "case-01")
CASE_10 = next(case for case in load_cases() if case["id"] == "case-10")


class FakeChat:
    def __init__(self, verdict: Any) -> None:
        self.verdict = verdict
        self.calls = 0

    def with_structured_output(self, schema: type) -> "FakeChat":
        return self

    def invoke(self, messages: Any) -> Any:
        self.calls += 1
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


def test_mode_names_and_resume_command() -> None:
    assert mode_name("rules", False) == "rules" and mode_name("rules", True) == "rules"
    assert mode_name("llm", False) == "llm" and mode_name("agent", True) == "agent-gold"
    assert cache_dir_for("rules") is None and cache_dir_for("agent-gold").name == "agent-gold"
    assert resume_command("agent", True) == "DECIDE_MODE=agent EVAL_GOLD_FIELDS=1 .venv/bin/python evals/run_evals.py"
    assert is_daily_quota({"llm_error_kind": "daily_quota"}) and is_daily_quota({"llm_error": "429 ...PerDay..."})
    assert not is_daily_quota({"llm_error": "429 per minute", "llm_error_kind": "transient"})


def test_llm_answer_is_cached_and_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_MODE", "llm")
    chat = FakeChat(Verdict(decision="approve", citations=[], reason="nothing fires"))
    monkeypatch.setattr(nodes, "get_chat_model", lambda: chat)
    graph = build_graph()
    first = run_case(graph, CASE_01, cache_dir=tmp_path / "llm")
    assert first["answered"] and not first["cached"] and chat.calls == 1
    record = json.loads((tmp_path / "llm" / "case-01.json").read_text(encoding="utf-8"))
    assert record["decision"] == "approve" and record["mode"] == "llm" and record["decide_audit"]["node"] == "decide"
    assert "extracted_fields" not in record  # extraction is never cached

    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(RuntimeError("must not be called")))
    second = run_case(graph, CASE_01, cache_dir=tmp_path / "llm")
    assert second["cached"] and second["answered"] and second["decision"] == (1, 1)
    assert second["fields"] == first["fields"]  # extraction still scored live


def test_daily_quota_stops_the_run_without_caching_or_counting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_MODE", "llm")
    long_tail = "x" * 300  # the quota id sits far into Google's error text, past the 200-char cut the audit keeps
    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(RuntimeError(f"429 RESOURCE_EXHAUSTED {long_tail} GenerateRequestsPerDayPerProjectPerModel-FreeTier")))
    rows, stopped = run_all(build_graph(), [CASE_10, CASE_01], False, tmp_path / "llm")
    assert stopped is True
    assert rows[0]["quota_stop"] and not rows[0]["answered"] and rows[0]["got_decision"] == "re_kyc"  # rules fallback happened, not counted
    assert "PerDay" not in rows[0]["llm_error"]  # the stored text is cut; the kind flag carried the classification
    assert not rows[1]["answered"] and rows[1]["got_decision"] == "" and rows[1]["fields"][1] > 0  # extraction only
    assert rows[1]["decision_rules"] == (1, 1)  # the free rules baseline is still scored after the stop
    assert not (tmp_path / "llm").exists()
    summary = summarise(rows)
    assert summary["answered"] == 0 and summary["complete"] is False and summary["decision_accuracy"] is None
    assert summary["extraction_total"] == rows[0]["fields"][1] + rows[1]["fields"][1]


def test_scorecard_prints_partial_and_what_citations_measure(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        {"id": "case-01", "tags": [], "expected_decision": "approve", "got_decision": "approve", "decision": (1, 1), "decision_rules": (1, 1),
         "citations": (0, 0), "fields": (9, 9), "fields_regex": (9, 9), "each_field": {}, "each_field_regex": {},
         "validate": (1, 1), "validate_extracted": (1, 1), "answered": True, "cached": True},
        {"id": "case-10", "tags": [], "expected_decision": "re_kyc", "got_decision": "re_kyc", "decision": (1, 1), "decision_rules": (1, 1),
         "citations": (2, 2), "fields": (8, 9), "fields_regex": (8, 9), "each_field": {}, "each_field_regex": {},
         "validate": (1, 1), "validate_extracted": (1, 1), "answered": False, "cached": False, "llm_error": "PerDay"},
    ]
    run_evals.print_scorecard(summarise(rows), rows, "off", "llm x", mode="llm")
    out = capsys.readouterr().out
    assert "decision accuracy       partial: 1/2 cases answered in mode llm" in out
    assert "LLM answers cached      1/2 reused" in out and "LLM not answered        case-10" in out
    assert "citation hit rate       partial: 1/2" in out and "measures: share of each case's expected paragraphs" in out
    assert "re_kyc -> (not answered)" in out


def test_cached_answers_are_still_used_after_a_quota_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECIDE_MODE", "llm")
    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(Verdict(decision="approve", citations=[], reason="ok")))
    graph = build_graph()
    run_case(graph, CASE_01, cache_dir=tmp_path / "llm")  # caches case-01
    monkeypatch.setattr(nodes, "get_chat_model", lambda: FakeChat(RuntimeError("429 GenerateRequestsPerDayPerProjectPerModel-FreeTier")))
    rows, stopped = run_all(graph, [CASE_10, CASE_01], False, tmp_path / "llm")
    assert stopped and not rows[0]["answered"]
    assert rows[1]["cached"] and rows[1]["answered"] and rows[1]["got_decision"] == "approve"
