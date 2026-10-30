"""Sessions, queries and dialogue turns, kept in SQLite.

Why not a dict: a restart used to erase every pending clarification and every
query id, so a client holding ``query_id`` got 404 and had to start over.  The
graph checkpoint is persistent too (``app.memory.checkpointer``), and the two
have to agree — a checkpoint without a query record is unreachable.

Rows are pruned after ``settings.session_ttl_hours``; ``prune`` also returns the
expired query ids so the caller can drop the matching checkpoint threads.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

MAX_HISTORY_TURNS = 50


@dataclass
class QueryRecord:
    query_id: str
    session_id: str
    question: str
    status: str = "pending"
    sql: str | None = None


@dataclass
class Session:
    session_id: str
    user_id: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    query_ids: list[str] = field(default_factory=list)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
CREATE TABLE IF NOT EXISTS queries (
    query_id   TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    question   TEXT NOT NULL,
    status     TEXT NOT NULL,
    sql        TEXT,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS queries_session ON queries(session_id);
"""


class SessionRepository:
    """SQLite-backed session/query index shared by every worker process."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        ttl_hours: float | None = None,
    ) -> None:
        self._db_path = str(db_path or getattr(settings, "session_db_path", "data/sessions.db"))
        self._ttl_hours = float(
            ttl_hours if ttl_hours is not None else getattr(settings, "session_ttl_hours", 24)
        )
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # -- plumbing ----------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """Opened lazily so importing the app never touches the disk."""
        if self._conn is None:
            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            # Shared across the threadpool; WAL keeps readers off the writer's
            # path and the timeout absorbs brief write contention.
            connection = sqlite3.connect(self._db_path, check_same_thread=False, timeout=30)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.commit()
            self._conn = connection
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- sessions ----------------------------------------------------------

    def create(self, session_id: str, user_id: str | None = None) -> Session:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, user_id, updated_at) VALUES (?, ?, ?)",
                (session_id, user_id, now),
            )
            self.conn.commit()
        return Session(session_id=session_id, user_id=user_id)

    def get_or_create(self, session_id: str, user_id: str | None = None) -> Session:
        return self.get(session_id) or self.create(session_id, user_id)

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT session_id, user_id FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            turns = self.conn.execute(
                "SELECT role, content FROM turns WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, MAX_HISTORY_TURNS),
            ).fetchall()
            query_ids = [
                item[0]
                for item in self.conn.execute(
                    "SELECT query_id FROM queries WHERE session_id = ? ORDER BY updated_at",
                    (session_id,),
                ).fetchall()
            ]
        return Session(
            session_id=row[0],
            user_id=row[1],
            history=[{"role": role, "content": content} for role, content in reversed(turns)],
            query_ids=query_ids,
        )

    # -- queries -----------------------------------------------------------

    def create_query(self, query_id: str, session_id: str, question: str) -> QueryRecord:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO queries "
                "(query_id, session_id, question, status, sql, updated_at) "
                "VALUES (?, ?, ?, 'pending', NULL, ?)",
                (query_id, session_id, question, now),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, user_id, updated_at) VALUES (?, NULL, ?)",
                (session_id, now),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self.conn.commit()
        return QueryRecord(query_id=query_id, session_id=session_id, question=question)

    def get_query(self, query_id: str) -> QueryRecord | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT query_id, session_id, question, status, sql FROM queries WHERE query_id = ?",
                (query_id,),
            ).fetchone()
        if row is None:
            return None
        return QueryRecord(
            query_id=row[0],
            session_id=row[1],
            question=row[2],
            status=row[3],
            sql=row[4],
        )

    def update_query(self, query_id: str, status: str, sql: str | None = None) -> None:
        with self._lock:
            if sql is None:
                self.conn.execute(
                    "UPDATE queries SET status = ?, updated_at = ? WHERE query_id = ?",
                    (status, time.time(), query_id),
                )
            else:
                self.conn.execute(
                    "UPDATE queries SET status = ?, sql = ?, updated_at = ? WHERE query_id = ?",
                    (status, sql, time.time(), query_id),
                )
            self.conn.commit()

    # -- history -----------------------------------------------------------

    def append_turn(self, session_id: str, role: str, content: str) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, user_id, updated_at) VALUES (?, NULL, ?)",
                (session_id, now),
            )
            self.conn.execute(
                "INSERT INTO turns (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            self.conn.commit()

    # -- retention ---------------------------------------------------------

    def prune(self, now: float | None = None) -> list[str]:
        """Drop rows older than the TTL; returns the expired query ids."""
        cutoff = (now if now is not None else time.time()) - self._ttl_hours * 3600
        with self._lock:
            expired = [
                row[0]
                for row in self.conn.execute(
                    "SELECT query_id FROM queries WHERE updated_at < ?", (cutoff,)
                ).fetchall()
            ]
            self.conn.execute("DELETE FROM queries WHERE updated_at < ?", (cutoff,))
            self.conn.execute("DELETE FROM turns WHERE created_at < ?", (cutoff,))
            self.conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            self.conn.commit()
        return expired


repository = SessionRepository()
