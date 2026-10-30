"""Undo the OCR confusions that matter for ID numbers: O for 0, and I or l for 1.

Only runs of two or more characters drawn from [0-9OIl] that already contain at least one
real digit are touched. Words such as "OIL", "Bill" or "INDIA" stay as printed, while
"7O42", "11OO92", "O3-11-1975" and the "l234" inside "DKEPPl234Q" are repaired.
"""

import re

_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "l": "1"})
_DIGIT_RUN = re.compile(r"[0-9OIl]{2,}")


def _fix_run(match: re.Match[str]) -> str:
    run = match.group()
    if not any(char.isdigit() for char in run):
        return run
    return run.translate(_LOOKALIKES)


def fix_digit_noise(text: str) -> str:
    """Repair digit look-alikes inside digit runs; everything else comes back unchanged."""
    return _DIGIT_RUN.sub(_fix_run, text)
