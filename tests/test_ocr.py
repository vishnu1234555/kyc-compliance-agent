import pytest

from kyc_agent.ocr import fix_digit_noise


@pytest.mark.parametrize(
    ("noisy", "clean"),
    [
        ("XXXX XXXX 7O42", "XXXX XXXX 7042"),
        ("PIN: 11OO92", "PIN: 110092"),
        ("Date of Birth: O3-11-1975", "Date of Birth: 03-11-1975"),
        ("DKEPPl234Q", "DKEPP1234Q"),
        ("WLJPKI204T", "WLJPK1204T"),
    ],
)
def test_digit_lookalikes_are_fixed_inside_digit_runs(noisy: str, clean: str) -> None:
    assert fix_digit_noise(noisy) == clean


@pytest.mark.parametrize("text", ["OIL", "Bill date", "INDIA", "S/O Prakash", "I declare that I do not", "POOJA NAIR", "Hill Road"])
def test_words_without_digits_are_left_alone(text: str) -> None:
    assert fix_digit_noise(text) == text
