from collections import Counter

from evals.run_evals import load_cases
from evals.split import load_split, make_split
from evals.sweep_threshold import choose

CASES = load_cases()


def test_split_file_matches_the_rule_and_is_balanced() -> None:
    split = make_split(CASES)
    assert load_split() == {"rule": load_split()["rule"], **split}
    tune, test = set(split["tune"]), set(split["test"])
    assert len(tune) == len(test) == 15 and not tune & test and tune | test == {case["id"] for case in CASES}
    unlabelled = Counter("tune" if case["id"] in tune else "test" for case in CASES if "unlabelled" in case["tags"])
    assert abs(unlabelled["tune"] - unlabelled["test"]) <= 1


def test_choose_prefers_more_hits_then_the_higher_threshold() -> None:
    assert choose({0.2: (10, 12), 0.3: (10, 12), 0.5: (9, 12)}) == 0.3
    assert choose({0.2: (11, 12), 0.9: (10, 12)}) == 0.2
