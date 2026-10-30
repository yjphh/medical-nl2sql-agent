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


def test_dimensions_and_enums_are_usable(tmp_path):
    """Regression: dept_name was NULL for every visit and the operation period
    code violated the schema enum, so department/elective questions were empty."""
    import json as _json

    db_path = tmp_path / "medical.db"
    build(db_path=db_path)
    schema = _json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    connection = sqlite3.connect(db_path)
    try:
        nulls = connection.execute(
            "SELECT COUNT(1) FROM visit WHERE dept_name IS NULL OR dept_name = ''"
        ).fetchone()[0]
        assert nulls == 0
        departments = connection.execute(
            "SELECT COUNT(DISTINCT dept_name) FROM visit"
        ).fetchone()[0]
        assert departments >= 2

        column = next(
            col
            for table in schema["tables"]
            if table["name"] == "operation_record"
            for col in table["columns"]
            if col["name"] == "operation_period_type_code"
        )
        allowed = {item["value"] for item in column.get("enum_values") or []}
        emitted = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT operation_period_type_code FROM operation_record"
            )
        }
        assert emitted and emitted <= allowed
    finally:
        connection.close()


def test_date_window_covers_reference_month(tmp_path):
    """Data must reach the month before ``reference_date`` so 上个月 is non-empty,
    while still covering 2025-01 for the SQLite examples."""
    db_path = tmp_path / "medical.db"
    build(db_path=db_path)
    connection = sqlite3.connect(db_path)
    try:
        months = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT strftime('%Y-%m', treatment_occurrence_time) FROM visit"
            )
        }
    finally:
        connection.close()
    assert "2025-01" in months
    assert "2025-11" in months
    assert "2025-12" in months


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
