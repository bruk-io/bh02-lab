import pytest
from report import total_by_category

HEADER = "date,description,category,amount\n"


def test_plain_rows():
    text = HEADER + "2026-01-01,Coffee,food,3.50\n2026-01-02,Bus,travel,2.75\n2026-01-03,Bagel,food,2.00\n"
    assert total_by_category(text) == {"food": pytest.approx(5.5), "travel": pytest.approx(2.75)}


def test_quoted_comma_in_description():
    text = HEADER + '2026-01-01,"Lunch, team",food,40.00\n2026-01-02,Taxi,travel,15\n'
    assert total_by_category(text) == {"food": pytest.approx(40.0), "travel": pytest.approx(15.0)}


def test_quoted_quote_in_description():
    text = HEADER + '2026-01-01,"The ""good"" pens",office,9.99\n'
    assert total_by_category(text) == {"office": pytest.approx(9.99)}


def test_blank_lines_are_ignored():
    text = HEADER + "\n2026-01-01,Coffee,food,3\n\n2026-01-02,Tea,food,2\n\n"
    assert total_by_category(text) == {"food": pytest.approx(5.0)}


def test_header_only_is_empty():
    assert total_by_category(HEADER) == {}
