"""Pick the PII-model threshold on the tune half and report it on the test half.

Run: .venv/bin/python evals/sweep_threshold.py [threshold ...]   (default 0.20 0.30 0.40 0.50 0.70 0.90 0.99)
Loads the real model once. For every threshold it prints extraction hits on the tune half, the
test half and all 30 cases. The chosen threshold is the best on the tune half (ties go to the
higher threshold, for precision); its test-half number is the honest one. The best full-30
threshold is printed too, labelled "tuned on itself", because that is what the first sweep did.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so "evals" imports when run as a script

from evals.run_evals import FIELDS, load_cases, score_each_field  # noqa: E402
from evals.split import load_split  # noqa: E402
from kyc_agent.config import get_settings  # noqa: E402
from kyc_agent.extraction import extract_fields  # noqa: E402
from kyc_agent.pii_model import GLiNERReader  # noqa: E402

Score = tuple[int, int]


def choose(results: dict[float, Score]) -> float:
    """The threshold with the most hits; on a tie, the higher threshold."""
    return max(results, key=lambda threshold: (results[threshold][0], threshold))


def main() -> None:
    thresholds = [float(value) for value in sys.argv[1:]] or [0.2, 0.3, 0.4, 0.5, 0.7, 0.9, 0.99]
    cases = load_cases()
    split = load_split()
    halves = {"tune": set(split["tune"]), "test": set(split["test"])}
    reader = GLiNERReader(get_settings().pii_model, thresholds[0])
    tune: dict[float, Score] = {}
    test: dict[float, Score] = {}
    full: dict[float, Score] = {}
    for threshold in thresholds:
        reader.threshold = threshold
        hits = {"tune": Counter(), "test": Counter()}
        totals = {"tune": Counter(), "test": Counter()}
        for case in cases:
            half = "tune" if case["id"] in halves["tune"] else "test"
            fields = extract_fields(case["raw_document"], reader).fields
            for name, (hit, total) in score_each_field(case["expected_fields"], fields).items():
                hits[half][name] += hit
                totals[half][name] += total
        tune[threshold] = (sum(hits["tune"].values()), sum(totals["tune"].values()))
        test[threshold] = (sum(hits["test"].values()), sum(totals["test"].values()))
        full[threshold] = (tune[threshold][0] + test[threshold][0], tune[threshold][1] + test[threshold][1])
        per_field = "  ".join(f"{name} {hits['tune'][name] + hits['test'][name]}/{totals['tune'][name] + totals['test'][name]}"
                              for name in FIELDS if totals["tune"][name] + totals["test"][name])
        print(f"threshold {threshold:.2f}: tune {tune[threshold][0]}/{tune[threshold][1]}  test {test[threshold][0]}/{test[threshold][1]}"
              f"  all {full[threshold][0]}/{full[threshold][1]}  | {per_field}")
    chosen = choose(tune)
    self_tuned = choose(full)
    reader.threshold = chosen  # so the description names the chosen threshold, not the last one swept
    print(f"PII model: {reader.describe()}")
    print(f"chosen on the tune half ({len(halves['tune'])} cases): threshold {chosen:.2f} -> test half {test[chosen][0]}/{test[chosen][1]} = {test[chosen][0] / test[chosen][1]:.4f}")
    print(f"tuned on itself (all 30, what the first sweep did): threshold {self_tuned:.2f} -> {full[self_tuned][0]}/{full[self_tuned][1]} = {full[self_tuned][0] / full[self_tuned][1]:.4f}")
    print(f"current default PII_THRESHOLD={get_settings().pii_threshold:.2f}; {'no change needed' if abs(get_settings().pii_threshold - chosen) < 1e-9 else 'CHANGE the default to ' + f'{chosen:.2f}'}")


if __name__ == "__main__":
    main()
