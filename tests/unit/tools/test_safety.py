import json
from pathlib import Path

from app.tools.sql_agent.safety import check_sql

EXAMPLES = Path(__file__).resolve().parents[3] / "data" / "sql_examples_sqlite.json"


def _examples() -> list[dict]:
    return json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]


def test_all_sqlite_examples_pass_safety():
    for example in _examples():
        result = check_sql(example["sql"])
        assert result.safe, (example["id"], result.reason, result.pii_columns)


def test_write_and_multiple_statements_rejected():
    assert not check_sql("DELETE FROM visit").safe
    assert not check_sql("SELECT 1; SELECT 2").safe
    assert not check_sql("PRAGMA table_info(visit)").safe
    assert check_sql("SELECT 1;").safe


def test_pii_rejected_except_inside_count():
    assert not check_sql("SELECT id_card FROM visit").safe
    assert not check_sql("SELECT MIN(id_card) FROM visit").safe
    assert check_sql("SELECT COUNT(DISTINCT id_card) AS c FROM visit").safe


def test_cte_names_are_not_treated_as_tables():
    sql = "WITH x AS (SELECT visit_id FROM visit) SELECT COUNT(1) AS c FROM x"
    result = check_sql(sql, allowed_tables=["visit"])
    assert result.safe, result.reason
    assert result.tables == ["visit"]


def test_unknown_table_rejected():
    result = check_sql("SELECT 1 FROM nope", allowed_tables=["visit"])
    assert not result.safe
    assert "nope" in result.reason


def test_select_star_is_pii_risk():
    result = check_sql("SELECT * FROM visit")
    assert not result.safe
    assert result.reason == "select_star_pii_risk"


def test_qualified_star_is_also_a_pii_risk():
    # ``t.*`` parses as a Column wrapping a Star, not a bare exp.Star.
    for sql in ("SELECT t.* FROM visit t", "SELECT v.*, 1 FROM visit v"):
        result = check_sql(sql)
        assert not result.safe, sql
        assert result.reason == "select_star_pii_risk", sql


def test_every_union_branch_is_inspected():
    sql = "SELECT 1 AS n UNION ALL SELECT id_card FROM visit"
    result = check_sql(sql)
    assert not result.safe
    assert result.pii_columns == ["id_card"]


def test_pii_in_group_or_order_or_having_is_rejected():
    for sql in (
        "SELECT COUNT(1) AS c FROM visit GROUP BY id_card",
        "SELECT COUNT(1) AS c FROM visit ORDER BY id_card",
        "SELECT COUNT(1) AS c FROM visit GROUP BY activity_type_code HAVING MAX(id_card) > 1",
    ):
        result = check_sql(sql)
        assert not result.safe, sql
        assert "id_card" in result.pii_columns, sql


def test_pii_filters_and_count_distinct_stay_allowed():
    # Filtering by a PII value reveals nothing; counting it is the documented
    # exception.
    assert check_sql("SELECT COUNT(1) AS c FROM visit WHERE id_card = 'x'").safe
    assert check_sql("SELECT COUNT(DISTINCT id_card) AS c FROM visit").safe
