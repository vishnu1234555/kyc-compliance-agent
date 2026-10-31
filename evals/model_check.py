"""One case, with and without the PII model, names side by side. No test, no scorecard: a look.

Run: .venv/bin/python evals/model_check.py [case-id]      (default case-24)
Prints the model id, the Hugging Face revision that was loaded and the load time, then the
`name` field and every document's name from the regex reader alone and from regex + model.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so "evals" imports when run as a script

from evals.run_evals import load_cases  # noqa: E402
from kyc_agent.config import DEFAULT_PII_MODEL, get_settings  # noqa: E402
from kyc_agent.extraction import extract_fields  # noqa: E402
from kyc_agent.pii_model import GLiNERReader, NullReader  # noqa: E402


def main() -> None:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "case-24"
    case = next(case for case in load_cases() if case["id"] == case_id)
    settings = get_settings()
    model_id = settings.pii_model if settings.pii_model.strip().lower() not in ("", "off", "none") else DEFAULT_PII_MODEL
    model = GLiNERReader(model_id, settings.pii_threshold)

    regex_only = extract_fields(case["raw_document"], NullReader())
    with_model = extract_fields(case["raw_document"], model)

    print(f"{case_id}: expected name {case['expected_fields']['name']!r}")
    print(f"PII model: {model.describe()}")
    print(f"{'':<28} {'regex only':<26} {'regex + model':<26}")
    print(f"{'name (file)':<28} {str(regex_only.fields.get('name')):<26} {str(with_model.fields.get('name')):<26}")
    for left, right in zip(regex_only.documents, with_model.documents):
        print(f"{'  ' + left['kind'] + ' name':<28} {str(left.get('name')):<26} {str(right.get('name')):<26}")
    print(f"{'address (file)':<28} {str(regex_only.fields.get('address')):<26} {str(with_model.fields.get('address')):<26}")
    print(f"candidates from the model: {with_model.stats['model_candidates']}, kept after arbitration: {with_model.stats['kept']} (regex alone kept {regex_only.stats['kept']})")


if __name__ == "__main__":
    main()
