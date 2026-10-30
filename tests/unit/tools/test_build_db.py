import json
import sqlite3
from pathlib import Path

from app.tools.sql_agent.build_db import DEFAULT_SCHEMA_PATH, build
from app.tools.sql_agent.executor import execute_readonly

EXAMPLES = Path(__file__).resolve().parents[3] / "data" / "sql_examples_sqlite.json"


def _examples() -> list[dict]:
    return json.loads(EXAMPLES.read_text(encoding="utf-8"))["examples"]


def test_build_creates_every_table_with_rows(tmp_path):
    db_path = tmp_path / "medical.db"
    counts = build(db_path=db_path)
    schema = json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert {table["name"] for table in schema["tables"]} == set(counts)
    assert all(count > 0 for count in counts.values())


def test_all_examples_execute_on_mirror(tmp_path):
    db_path = tmp_path / "medical.db"
    build(db_path=db_path)
    for example in _examples():
        result = execute_readonly(db_path, example["sql"])
        assert result.ok, (example["id"], result.error)
        assert result.row_count > 0, example["id"]


def test_join_keys_line_up(tmp_path):
    db_path = tmp_path / "medical.db"
    build(db_path=db_path)
    connection = sqlite3.connect(db_path)
    try:
        orphan = connection.execute(
            "SELECT COUNT(1) FROM medical_expense_rec m "
            "LEFT JOIN visit v ON v.visit_id = m.visit_id WHERE v.visit_id IS NULL"
        ).fetchone()[0]
        assert orphan == 0
        orphan = connection.execute(
            "SELECT COUNT(1) FROM examination_exam e "
            "LEFT JOIN examination_record r ON r.dcid = e.dcid WHERE r.dcid IS NULL"
        ).fetchone()[0]
        assert orphan == 0
    finally:
        connection.close()


def test_executor_is_read_only(tmp_path):
    db_path = tmp_path / "medical.db"
    build(db_path=db_path)
    result = execute_readonly(db_path, "DELETE FROM visit")
    assert not result.ok
    assert "readonly" in result.error.lower() or "read-only" in result.error.lower()


def test_executor_reports_missing_database(tmp_path):
    result = execute_readonly(tmp_path / "nope.db", "SELECT 1")
    assert not result.ok
    assert "database_not_found" in result.error
