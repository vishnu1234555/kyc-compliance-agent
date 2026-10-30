"""Retry on rate limits and outages with a fixed waiting schedule. Used for every Gemini call."""

import logging
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

BACKOFF_SECONDS = (5, 15, 30, 60, 60, 60)
_TRANSIENT_MARKS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "deadline", "Deadline", "Server disconnected")


def is_transient(exc: Exception) -> bool:
    """Worth retrying: per-minute rate limits and outages. A spent daily quota is not."""
    text = str(exc)
    if "PerDay" in text:  # Gemini free tier: GenerateRequestsPerDayPerProjectPerModel
        return False
    return any(mark in text for mark in _TRANSIENT_MARKS)


def with_backoff(call: Callable[[], T], what: str, schedule: tuple[int, ...] = BACKOFF_SECONDS) -> T:
    """Run `call`; on a rate limit or outage wait and retry on the schedule, then try once more."""
    for attempt, wait in enumerate(schedule):
        try:
            return call()
        except Exception as exc:  # the google client raises several types for 429/503
            if not is_transient(exc):
                raise
            logger.warning("%s: %s (attempt %d); retrying in %ds", what, type(exc).__name__, attempt + 1, wait)
            time.sleep(wait)
    return call()
