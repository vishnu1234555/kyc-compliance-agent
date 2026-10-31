"""Checkpointers that never store a full Aadhaar number.

LangGraph writes a checkpoint after every node. The one taken after `extract` would hold the
Aadhaar number as printed on the card (validate masks it one node later). Instead of changing
what extract hands to validate, the number is masked at the only place checkpoint data is
written: the serializer. `MaskingSerializer` rewrites every 12-digit run in strings to
`XXXX-XXXX-<last four>` before `JsonPlusSerializer` encodes it, for checkpoints, pending writes
and channel blobs alike, in memory and in SQLite. LangGraph's own bookkeeping (ids, channel
versions) is left untouched so the graph can still find its checkpoints.
"""

import logging
import re
import sqlite3
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from kyc_agent.tracing import mask_text

logger = logging.getLogger(__name__)

# Keys whose values are LangGraph bookkeeping, not KYC data: never rewritten.
_BOOKKEEPING_KEYS = frozenset({
    "id", "ts", "v", "checkpoint_id", "parent_checkpoint_id", "thread_id", "checkpoint_ns",
    "task_id", "task_path", "channel_versions", "versions_seen", "step", "source", "parents",
})
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def mask_data(value: Any, key: str | None = None) -> Any:
    """A copy of `value` with every 12-digit run in its strings masked; dicts, lists, tuples and
    sets are walked, anything else is returned as it is."""
    if key in _BOOKKEEPING_KEYS:
        return value
    if isinstance(value, str):
        return value if _UUID.match(value) else mask_text(value)
    if isinstance(value, dict):
        return {k: mask_data(v, k if isinstance(k, str) else None) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_data(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_data(v) for v in value)
    if isinstance(value, set):
        return {mask_data(v) for v in value}
    return value


class MaskingSerializer(JsonPlusSerializer):
    """LangGraph's default serializer, with the Aadhaar mask applied first."""

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        return super().dumps_typed(mask_data(obj))


def memory_checkpointer() -> BaseCheckpointSaver:
    return MemorySaver(serde=MaskingSerializer())


def sqlite_checkpointer(path: str) -> BaseCheckpointSaver:
    """Checkpoints in a SQLite file, so a reviewer can resume after a restart; masked on the way in."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    logger.info("checkpoints in %s", path)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=MaskingSerializer())
