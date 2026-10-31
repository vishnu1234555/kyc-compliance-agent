"""The one place that builds a Gemini chat model, so every node uses the same settings."""

import logging
from typing import Any

from kyc_agent.config import get_settings

logger = logging.getLogger(__name__)


def get_chat_model(**overrides: Any) -> Any:
    """ChatGoogleGenerativeAI at temperature 0. The API key comes from GOOGLE_API_KEY only."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # imported here so tests never need it

    settings = get_settings()
    options: dict[str, Any] = {
        "model": settings.gemini_model,
        "temperature": 0,
        "max_retries": 1,  # our own backoff handles the long waits
    }
    if settings.gemini_thinking_budget is not None:
        options["thinking_budget"] = settings.gemini_thinking_budget
    options.update(overrides)
    logger.debug("chat model %s", options["model"])
    return ChatGoogleGenerativeAI(**options)
