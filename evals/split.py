"""A fixed tune/test split of the 30 cases, balanced on the "unlabelled" tag.

Rule (deterministic, so evals/split.json can be regenerated and checked by a test): within each
tag group (unlabelled, labelled) sort the case ids; alternate the halves; the unlabelled group
starts with tune and the labelled group starts with test, which gives 15 cases each with 9 vs 8
unlabelled. Anything tuned on the tune half (the PII-model threshold) is reported on the test
half, where it has never been looked at.
"""

import json
from pathlib import Path
from typing import Any

SPLIT_PATH = Path(__file__).resolve().parent / "split.json"


def make_split(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    unlabelled = sorted(case["id"] for case in cases if "unlabelled" in case.get("tags", []))
    labelled = sorted(case["id"] for case in cases if "unlabelled" not in case.get("tags", []))
    tune = unlabelled[0::2] + labelled[1::2]
    test = unlabelled[1::2] + labelled[0::2]
    return {"tune": sorted(tune), "test": sorted(test)}


def load_split(path: Path = SPLIT_PATH) -> dict[str, list[str]]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_split(cases: list[dict[str, Any]], path: Path = SPLIT_PATH) -> dict[str, list[str]]:
    split = make_split(cases)
    path.write_text(json.dumps({"rule": "see evals/split.py: sorted within tag group, alternating halves", **split}, indent=2) + "\n", encoding="utf-8")
    return split
