from datetime import date

import pytest

from kyc_agent.rules import add_years, compare_across_documents, kyc_due

AS_OF = date(2026, 10, 30)


@pytest.mark.parametrize(
    ("risk", "last", "due", "overdue"),
    [
        ("high", "15-06-2023", "15-06-2025", True),
        ("high", "09-03-2026", "09-03-2028", False),
        ("medium", "11-11-2016", "11-11-2024", True),
        ("medium", "14-02-2022", "14-02-2030", False),
        ("low", "20-01-2015", "20-01-2025", True),
        ("low", "03-07-2020", "03-07-2030", False),
        ("High", "30-10-2024", "30-10-2026", False),  # due today is not overdue
    ],
)
def test_kyc_due_dates(risk: str, last: str, due: str, overdue: bool) -> None:
    result = kyc_due(risk, last, as_of=AS_OF)
    assert result["due_date"] == due
    assert result["overdue"] is overdue
    assert result["as_of"] == "30-10-2026"


def test_leap_day_and_bad_input() -> None:
    assert add_years(date(2020, 2, 29), 2) == date(2022, 2, 28)
    assert "error" in kyc_due("very high", "01-01-2020", as_of=AS_OF)
    assert "error" in kyc_due("low", "2020-01-01", as_of=AS_OF)


def test_compare_across_documents() -> None:
    documents = [
        {"kind": "passport", "name": "ANJALI MEHRA", "dob": "12-02-1987"},
        {"kind": "pan_card", "name": "ANJALI MALHOTRA", "dob": "12-02-1987"},
        {"kind": "note"},
    ]
    names = compare_across_documents(documents, "name")
    assert names["mismatch"] is True
    assert names["distinct"] == ["ANJALI MALHOTRA", "ANJALI MEHRA"]
    assert [entry["document"] for entry in names["values"]] == ["passport", "pan_card"]
    assert compare_across_documents(documents, "dob")["mismatch"] is False
    assert compare_across_documents(documents, "pan") == {"field": "pan", "values": [], "distinct": [], "mismatch": False}
