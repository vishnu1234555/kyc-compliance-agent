"""The KYC graph: extract -> validate -> retrieve_rules -> decide -> (human_review) -> END.

A checkpointer is always compiled in, because human_review pauses with interrupt() and the
graph must be able to pick the file up again. In memory by default; set CHECKPOINT_DB to a
SQLite path to survive a restart. Every invoke needs a thread id (run_config gives one).
"""

import logging
import sqlite3
import uuid
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from kyc_agent import nodes
from kyc_agent.config import get_settings
from kyc_agent.state import KYCState
from kyc_agent.tracing import tracing_callbacks

logger = logging.getLogger(__name__)


def default_checkpointer() -> BaseCheckpointSaver:
    """SQLite when CHECKPOINT_DB is set (a reviewer can resume after a restart), else in memory."""
    path = get_settings().checkpoint_db
    if path:
        from langgraph.checkpoint.sqlite import SqliteSaver

        logger.info("checkpoints in %s", path)
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    return MemorySaver()


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    builder: StateGraph = StateGraph(KYCState)
    builder.add_node("extract", nodes.extract)
    builder.add_node("validate", nodes.validate)
    builder.add_node("retrieve_rules", nodes.retrieve_rules)
    builder.add_node("decide", nodes.decide)
    builder.add_node("human_review", nodes.human_review)

    builder.add_edge(START, "extract")
    builder.add_edge("extract", "validate")
    builder.add_edge("validate", "retrieve_rules")
    builder.add_edge("retrieve_rules", "decide")
    # approve goes straight out; re_kyc and escalate go to a human
    builder.add_conditional_edges("decide", route_after_decide, {"human_review": "human_review", "end": END})
    builder.add_edge("human_review", END)
    return builder.compile(checkpointer=checkpointer or default_checkpointer())


def route_after_decide(state: KYCState) -> str:
    return "end" if state["decision"] == "approve" else "human_review"


def run_config(thread_id: str | None = None) -> dict[str, Any]:
    """The config every invoke/stream needs: a thread id for the checkpointer (one per KYC file)
    and, when LANGSMITH_API_KEY is set, the LangSmith tracer that masks 12-digit numbers."""
    config: dict[str, Any] = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
    callbacks = tracing_callbacks()
    if callbacks:
        config["callbacks"] = callbacks
    return config
