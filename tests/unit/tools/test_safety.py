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
