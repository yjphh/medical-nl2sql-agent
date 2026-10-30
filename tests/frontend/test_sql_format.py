"""SQL display formatting: multi-line output, never raises."""

from __future__ import annotations

from frontend.sql_format import format_sql

SQL = (
    "SELECT dept_name, COUNT(DISTINCT visit_id) AS visits FROM visit "
    "WHERE activity_type_code = '1' GROUP BY dept_name ORDER BY visits DESC"
)


def test_single_line_sql_is_split_into_clauses():
    formatted = format_sql(SQL)

    assert "\n" in formatted
    lines = [line.strip() for line in formatted.splitlines() if line.strip()]
    assert any(line.startswith("FROM") for line in lines)
    assert any(line.startswith("WHERE") for line in lines)
    assert any(line.startswith("GROUP BY") for line in lines)


def test_formatted_sql_keeps_every_identifier():
    formatted = format_sql(SQL).lower()

    for token in ("dept_name", "count", "visit_id", "activity_type_code", "group by"):
        assert token in formatted


def test_joins_are_not_split():
    sql = "SELECT COUNT(*) FROM visit v LEFT JOIN patient p ON p.id = v.pid"
    formatted = format_sql(sql)

    assert "LEFT JOIN" in formatted


def test_unparsable_sql_falls_back_without_raising():
    broken = "SELECT FROM WHERE ("

    formatted = format_sql(broken)

    assert formatted
    assert isinstance(formatted, str)


def test_empty_sql_returns_empty_string():
    assert format_sql("") == ""
    assert format_sql("   ") == ""
