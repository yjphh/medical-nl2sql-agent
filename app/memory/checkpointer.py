"""Graph checkpointer: one persistent thread state per query id.

``MemorySaver`` used to hold every query's state for the life of the process
and lose all of it on restart, so a client that kept ``query_id`` got 404 and
an in-flight clarification could not be resumed at all.  The SQLite saver keeps
the same state on disk and is shared by every worker process.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_CHECKPOINTER: Any | None = None
_CONNECTION: sqlite3.Connection | None = None


def _build() -> Any:
    path = str(getattr(settings, "checkpoint_db_path", "data/checkpoints.db"))
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning(
            "langgraph-checkpoint-sqlite is not installed; clarifications will "
            "be lost on restart (pip install langgraph-checkpoint-sqlite)"
        )
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    global _CONNECTION
    # FastAPI serves requests from a threadpool, so the connection must accept
    # use from any thread; WAL + a busy timeout let a checkpoint write wait for
    # a concurrent read instead of failing with "database is locked".
    _CONNECTION = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONNECTION.execute("PRAGMA journal_mode=WAL")
    saver = SqliteSaver(_CONNECTION)
    saver.setup()
    return saver


def get_checkpointer() -> Any:
    global _CHECKPOINTER
    if _CHECKPOINTER is None:
        _CHECKPOINTER = _build()
    return _CHECKPOINTER


def drop_thread(query_id: str) -> None:
    """Forget one query's state (used by retention cleanup)."""
    try:
        get_checkpointer().delete_thread(query_id)
    except Exception:
        logger.warning("could not delete checkpoint thread %s", query_id, exc_info=True)


def reset() -> None:
    """Drop the singleton (tests only)."""
    global _CHECKPOINTER, _CONNECTION
    if _CONNECTION is not None:
        _CONNECTION.close()
    _CHECKPOINTER = None
    _CONNECTION = None
