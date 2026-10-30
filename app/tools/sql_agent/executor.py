"""Read-only SQLite execution used for execution feedback and voting.

Only the local mirror is ever opened.  The connection is opened with
``mode=ro`` and ``PRAGMA query_only=ON``; a progress handler aborts queries
that exceed the deadline.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


class _QueryTimeout(Exception):
    pass


@dataclass
class ExecutionResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: int = 0
    error: str = ""


def execute_readonly(
    db_path: str | Path,
    sql: str,
    *,
    max_rows: int = 50,
    timeout_s: float = 5.0,
) -> ExecutionResult:
    """Execute ``sql`` against ``db_path`` and return at most ``max_rows`` rows.

    The result rows stay inside the caller; this module never sends them to an
    LLM.  Errors are returned as data, not raised.
    """
    started = time.perf_counter()
    deadline = started + timeout_s
    path = Path(db_path).resolve()
    if not path.exists():
        return ExecutionResult(ok=False, error=f"database_not_found: {path}")

    uri = f"file:{path.as_posix()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_s)
        conn.execute("PRAGMA query_only=ON")

        def _abort() -> int:
            if time.perf_counter() > deadline:
                raise _QueryTimeout
            return 0

        conn.set_progress_handler(_abort, 10_000)
        cursor = conn.execute(sql)
        columns = [item[0] for item in cursor.description or []]
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        rows = [tuple(row) for row in fetched[:max_rows]]
        return ExecutionResult(
            ok=True,
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=_elapsed_ms(started),
        )
    except _QueryTimeout:
        return ExecutionResult(
            ok=False,
            elapsed_ms=_elapsed_ms(started),
            error=f"query_timeout: exceeded {timeout_s}s",
        )
    except sqlite3.Error as exc:
        return ExecutionResult(ok=False, elapsed_ms=_elapsed_ms(started), error=str(exc))
    finally:
        if conn is not None:
            conn.set_progress_handler(None, 0)
            conn.close()


def sqlite_executor(
    db_path: str | Path,
    *,
    max_rows: int = 50,
    timeout_s: float = 5.0,
):
    """Return an ``executor(sql) -> ExecutionResult`` bound to one database."""

    def _run(sql: str) -> ExecutionResult:
        return execute_readonly(db_path, sql, max_rows=max_rows, timeout_s=timeout_s)

    return _run


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
