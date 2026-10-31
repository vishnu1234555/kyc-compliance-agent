import pytest

from kyc_agent import tracing


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("Aadhaar 9999 4512 7782 on file", "Aadhaar XXXX-XXXX-7782 on file"),
        ("9999-6543-2101", "XXXX-XXXX-2101"),
        ("raw 999933445565 digits", "raw XXXX-XXXX-5565 digits"),
        ("PAN ABCPE1234F PIN 560001 DL04 20150099812", "PAN ABCPE1234F PIN 560001 DL04 20150099812"),  # not 12 digits: untouched
        ("XXXX-XXXX-1234 stays", "XXXX-XXXX-1234 stays"),
    ],
)
def test_mask_text(text: str, masked: str) -> None:
    assert tracing.mask_text(text) == masked


def test_anonymizer_walks_nested_trace_data() -> None:
    anonymize = tracing.make_anonymizer()
    data = {"raw_document": "DOB: 06/06/1977\n9999 4512 7782\n", "documents": [{"aadhaar": "9999-4512-7782"}], "n": 2}
    assert anonymize(data) == {"raw_document": "DOB: 06/06/1977\nXXXX-XXXX-7782\n", "documents": [{"aadhaar": "XXXX-XXXX-7782"}], "n": 2}


def test_tracing_is_off_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "")
    tracing.langsmith_client.cache_clear()
    assert tracing.tracing_enabled() is False
    assert tracing.tracing_callbacks() == []
    tracing.langsmith_client.cache_clear()
