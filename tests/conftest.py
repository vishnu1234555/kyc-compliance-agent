"""Shared test setup: the unit tests never load the real PII model."""

import pytest

from kyc_agent import nodes
from kyc_agent.pii_model import NullReader


@pytest.fixture(autouse=True)
def no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nodes, "default_reader", lambda: NullReader())
