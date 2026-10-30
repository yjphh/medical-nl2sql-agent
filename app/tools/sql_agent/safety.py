"""Static safety checks for generated SQLite SQL.

Checks run before execution: single read-only statement, known tables, and no
PII column in the SELECT output (``COUNT(DISTINCT ...)`` is the one exception,
because the project uses it to count patients without exposing the value).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "data" / "schema.json"

# Columns flagged ``is_pii`` in schema.json.  Kept as names because the same
# column name (e.g. ``name``) is PII on every table that has it.
_PII_CACHE: set[str] | None = None


@dataclass
class SafetyResult:
    safe: bool
    reason: str = ""
    tables: list[str] = field(default_factory=list)
    pii_columns: list[str] = field(default_factory=list)


def pii_column_names(schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> set[str]:
    """Return every column name marked ``is_pii`` in the schema."""
    global _PII_CACHE
    if _PII_CACHE is not None:
        return _PII_CACHE
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    names = {
        column["name"].lower()
        for table in schema["tables"]
        for column in table["columns"]
        if column.get("is_pii")
    }
    _PII_CACHE = names
    return names


def check_sql(
    sql: str,
    *,
    allowed_tables: list[str] | None = None,
    pii_columns: set[str] | None = None,
) -> SafetyResult:
    """Validate one SQLite statement without touching the database."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return SafetyResult(safe=False, reason="empty_sql")

    try:
        statements = [stmt for stmt in sqlglot.parse(sql, read="sqlite") if stmt is not None]
    except sqlglot.errors.ParseError as exc:
        return SafetyResult(safe=False, reason=f"parse_error: {exc}")

    if len(statements) != 1:
        return SafetyResult(safe=False, reason="multiple_statements")
    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.Union)):
        return SafetyResult(safe=False, reason="only_select_allowed")

    cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    tables = sorted(
        {table.name.lower() for table in statement.find_all(exp.Table) if table.name}
        - cte_names
    )
    if allowed_tables is not None:
        unknown = sorted(set(tables) - {name.lower() for name in allowed_tables})
        if unknown:
            return SafetyResult(
                safe=False,
                reason=f"unknown_tables: {', '.join(unknown)}",
                tables=tables,
            )

    pii = pii_column_names() if pii_columns is None else pii_columns
    exposed: list[str] = []
    for projection in _projections(statement):
        expression = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(expression, exp.Count):
            continue
        if isinstance(expression, exp.Star):
            exposed.append("*")
            continue
        for column in expression.find_all(exp.Column):
            # PII is allowed inside COUNT(DISTINCT ...): it counts, it does not
            # reveal the value.
            if _under_count(column):
                continue
            if column.name.lower() in pii:
                exposed.append(column.name.lower())

    if exposed:
        reason = "select_star_pii_risk" if "*" in exposed else "pii_in_select"
        return SafetyResult(
            safe=False,
            reason=reason,
            tables=tables,
            pii_columns=sorted(set(exposed)),
        )
    return SafetyResult(safe=True, tables=tables)


def _projections(statement: exp.Expression) -> list[exp.Expression]:
    if isinstance(statement, exp.Select):
        return list(statement.expressions)
    select = statement.find(exp.Select)
    return list(select.expressions) if select is not None else []


def _under_count(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Count):
            return True
        parent = parent.parent
    return False
