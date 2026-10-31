"""LangSmith tracing: on when LANGSMITH_API_KEY is set, always through an anonymizer.

Traces carry every node's input and output, which before validate includes the Aadhaar number
as printed on the card. The anonymizer rewrites every 12-digit run in traced data to
XXXX-XXXX-<last four> before it leaves the process. The tracer is attached explicitly through
run_config() rather than the LANGSMITH_TRACING environment switch, because that switch would
start a second, un-anonymised tracer.
"""

import logging
import re
from functools import lru_cache
from typing import Any

from kyc_agent.config import get_settings

logger = logging.getLogger(__name__)

_TWELVE_DIGITS = re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")  # separators: space or hyphen, never a line break


def mask_text(text: str) -> str:
    """Every 12-digit run (with or without spaces or hyphens) becomes XXXX-XXXX-<last four>."""
    return _TWELVE_DIGITS.sub(lambda match: f"XXXX-XXXX-{re.sub(r'[^0-9]', '', match.group())[-4:]}", text)


def _replacer(text: str, path: list[Any]) -> str:
    return mask_text(text)


def make_anonymizer() -> Any:
    """LangSmith's anonymizer, walking nested inputs/outputs and masking every string."""
    from langsmith.anonymizer import create_anonymizer

    return create_anonymizer(_replacer)


def tracing_enabled() -> bool:
    return bool(get_settings().langsmith_api_key)


@lru_cache(maxsize=1)
def langsmith_client() -> Any:
    """One LangSmith client for the process, built with the anonymizer. None when tracing is off."""
    if not tracing_enabled():
        return None
    from langsmith import Client

    settings = get_settings()
    logger.info("LangSmith tracing on, project %s, anonymizer active", settings.langsmith_project)
    return Client(api_key=settings.langsmith_api_key, anonymizer=make_anonymizer())


def tracing_callbacks() -> list[Any]:
    """The callback list to pass in a run config: one anonymising tracer, or nothing."""
    client = langsmith_client()
    if client is None:
        return []
    from langchain_core.tracers.langchain import LangChainTracer

    return [LangChainTracer(project_name=get_settings().langsmith_project, client=client)]
