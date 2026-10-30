"""Try a few PII-model thresholds on the eval set and print extraction hits per field for each.

Run: .venv/bin/python evals/sweep_threshold.py [threshold ...]   (default 0.50 0.70 0.90 0.95 0.99)
Loads the real model once; the regex reader runs underneath every setting, so the
differences between rows are what the model adds or breaks at that threshold.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so "evals" imports when run as a script

from evals.run_evals import FIELDS, load_cases, score_each_field  # noqa: E402
from kyc_agent.config import get_settings
from kyc_agent.extraction import extract_fields
from kyc_agent.pii_model import GLiNERReader


def main() -> None:
    thresholds = [float(value) for value in sys.argv[1:]] or [0.5, 0.7, 0.9, 0.95, 0.99]
    cases = load_cases()
    reader = GLiNERReader(get_settings().pii_model, thresholds[0])
    for threshold in thresholds:
        reader.threshold = threshold
        hits: Counter[str] = Counter()
        totals: Counter[str] = Counter()
        for case in cases:
            fields = extract_fields(case["raw_document"], reader).fields
            for name, (hit, total) in score_each_field(case["expected_fields"], fields).items():
                hits[name] += hit
                totals[name] += total
        per_field = "  ".join(f"{name} {hits[name]}/{totals[name]}" for name in FIELDS if totals[name])
        print(f"threshold {threshold:.2f}: {sum(hits.values())}/{sum(totals.values())}  {per_field}")


if __name__ == "__main__":
    main()
