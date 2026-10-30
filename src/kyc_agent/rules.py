"""Deterministic helpers behind the decision rules in evals/POLICY.md section 3. No LLM, no I/O.

Dates are exact: LLMs get date arithmetic wrong, so periodic-updation maths lives here and is
exposed to the agent as a tool.
"""

import calendar
from datetime import date, datetime
from typing import Any

# RBI para 42(1): at least once in every 2 / 8 / 10 years for high / medium / low risk.
PERIOD_YEARS: dict[str, int] = {"high": 2, "medium": 8, "low": 10}

DATE_FORMAT = "%d-%m-%Y"


def parse_date(text: str) -> date:
    return datetime.strptime(text, DATE_FORMAT).date()


def add_years(day: date, years: int) -> date:
    """Same day `years` later; 29 February lands on 28 February when the target year is not leap."""
    year = day.year + years
    last_day = calendar.monthrange(year, day.month)[1]
    return day.replace(year=year, day=min(day.day, last_day))


def kyc_due(risk_category: str, last_kyc_date: str, as_of: date | None = None) -> dict[str, Any]:
    """When the next periodic KYC updation falls due, and whether it is overdue on `as_of`.

    Returns plain values so the result can go straight into the audit log or a tool reply.
    """
    risk = risk_category.strip().lower()
    today = as_of or date.today()
    if risk not in PERIOD_YEARS:
        return {"error": f"unknown risk category {risk_category!r}; expected low, medium or high"}
    try:
        last = parse_date(last_kyc_date)
    except ValueError:
        return {"error": f"last_kyc_date {last_kyc_date!r} is not DD-MM-YYYY"}
    due = add_years(last, PERIOD_YEARS[risk])
    return {
        "risk_category": risk,
        "last_kyc_date": last.strftime(DATE_FORMAT),
        "years_allowed": PERIOD_YEARS[risk],
        "due_date": due.strftime(DATE_FORMAT),
        "as_of": today.strftime(DATE_FORMAT),
        "overdue": today > due,
        "days_overdue": max((today - due).days, 0),
        "basis": "RBI NBFC KYC Directions 2025, para 42(1)",
    }


def compare_across_documents(documents: list[dict[str, Any]], field: str) -> dict[str, Any]:
    """That field's value in each document of the file, and whether the documents disagree."""
    values = [
        {"document": document.get("kind", "unknown"), "value": document[field]}
        for document in documents
        if document.get(field) not in (None, "")
    ]
    distinct = sorted({str(entry["value"]) for entry in values})
    return {"field": field, "values": values, "distinct": distinct, "mismatch": len(distinct) > 1}
