"""No checkpoint, in memory or in SQLite, may hold a full 12-digit Aadhaar number."""

import re
from pathlib import Path
from typing import Any

from langgraph.types import Command

from evals.run_evals import fresh_state, load_cases
from kyc_agent.checkpoints import MaskingSerializer, mask_data, memory_checkpointer, sqlite_checkpointer
from kyc_agent.graph import build_graph, run_config

# A 12-digit number as a token of its own (spaces or hyphens allowed), the same shape the mask looks for.
TWELVE_DIGITS = re.compile(rb"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")
# LangGraph channel versions look like "00000000000000000000000000000002.0.5078476676515129": a 32-digit
# counter and a random float. They are bookkeeping, not Aadhaar numbers, so they are removed before the search.
CHANNEL_VERSION = re.compile(rb"\d{32}\.\d+\.\d+")
# Checkpoint and run ids are UUIDs; one whose last group happens to be all digits is not an Aadhaar number either.
UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CASE_14 = next(case for case in load_cases() if case["id"] == "case-14")  # Aadhaar printed in full


def has_full_aadhaar(data: bytes) -> bool:
    return TWELVE_DIGITS.search(UUID.sub(b"", CHANNEL_VERSION.sub(b"", data))) is not None


def _all_bytes(value: Any) -> list[bytes]:
    """Every bytes object inside nested dicts, lists and tuples (the in-memory saver's stores)."""
    if isinstance(value, bytes):
        return [value]
    if isinstance(value, dict):
        return [b for v in value.values() for b in _all_bytes(v)]
    if isinstance(value, (list, tuple)):
        return [b for v in value for b in _all_bytes(v)]
    return []


def test_the_detector_itself() -> None:
    assert has_full_aadhaar(b"name\x00 9999 4512 7782 \x00") and has_full_aadhaar(b"a 999945127782 b")
    assert not has_full_aadhaar(b"XXXX-XXXX-7782 00000000000000000000000000000002.0.5078476676515129 DL04 20150099812")
    assert not has_full_aadhaar(b"id 1f1d5002-73f1-6d55-8000-123456789012 end")  # a UUID with a digit-only last group


def test_mask_data_masks_strings_everywhere_but_bookkeeping() -> None:
    data = {
        "raw_document": "DOB: 06/06/1977\n9999 4512 7782\n",
        "documents": [{"aadhaar": "9999-4512-7782", "n": 12}],
        "nested": ({"x": ["999945127782"]},),
        "id": "1f0b2c3d-1234-6789-0123-123456789012",
        "channel_versions": {"extracted_fields": "00000000000000000000000000000002.0.1234"},
    }
    masked = mask_data(data)
    assert masked["raw_document"] == "DOB: 06/06/1977\nXXXX-XXXX-7782\n"
    assert masked["documents"] == [{"aadhaar": "XXXX-XXXX-7782", "n": 12}]
    assert masked["nested"] == ({"x": ["XXXX-XXXX-7782"]},)
    assert masked["id"] == data["id"] and masked["channel_versions"] == data["channel_versions"]
    assert data["documents"][0]["aadhaar"] == "9999-4512-7782"  # the input is not changed
    kind, blob = MaskingSerializer().dumps_typed({"aadhaar": "9999 4512 7782"})
    assert b"9999" not in blob and b"XXXX-XXXX-7782" in blob


def test_sqlite_checkpoint_file_holds_no_full_aadhaar(tmp_path: Path) -> None:
    db = tmp_path / "checkpoints.sqlite"
    graph = build_graph(sqlite_checkpointer(str(db)))
    config = run_config("case-14-review")
    paused = graph.invoke(fresh_state(CASE_14["raw_document"]), config=config)
    # the live run still saw the number as printed: validate recorded the error exactly as before
    assert paused["validation_errors"] == ["Aadhaar must be masked as XXXX-XXXX-1234"]
    assert paused["extracted_fields"]["aadhaar"] == "XXXX-XXXX-7782" and paused["decision"] == "re_kyc"
    assert "__interrupt__" in paused  # paused at human_review, so every earlier checkpoint is on disk
    files = {file.name: file.read_bytes() for file in tmp_path.iterdir()}  # the database and its WAL/journal files
    for name, data in files.items():
        assert not has_full_aadhaar(data), f"full Aadhaar number inside {name}"
    assert any(b"XXXX-XXXX-7782" in data for data in files.values())  # the masked form is what was stored
    # the stored state is usable: resume and finish
    final = graph.invoke(Command(resume={"reviewer": "test", "action": "confirm"}), config=config)
    assert final["audit_log"][-1]["node"] == "human_review" and final["decision"] == "re_kyc"
    assert not any(has_full_aadhaar(file.read_bytes()) for file in tmp_path.iterdir())


def test_memory_checkpointer_holds_no_full_aadhaar() -> None:
    saver = memory_checkpointer()
    graph = build_graph(saver)
    paused = graph.invoke(fresh_state(CASE_14["raw_document"]), config=run_config("case-14-memory"))
    assert paused["validation_errors"] == ["Aadhaar must be masked as XXXX-XXXX-1234"]
    blobs = [b for name in ("storage", "blobs", "writes") for b in _all_bytes(getattr(saver, name, {}))]
    assert blobs, "the in-memory saver stored nothing"
    assert not any(has_full_aadhaar(blob) for blob in blobs)
    assert any(b"XXXX-XXXX-7782" in blob for blob in blobs)  # the masked form is what was stored
