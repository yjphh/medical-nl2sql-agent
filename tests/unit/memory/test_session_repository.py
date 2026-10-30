"""The session/query index has to outlive the process, or a restart loses
every pending clarification the client is still holding a ``query_id`` for."""

from __future__ import annotations

import time

from app.memory.session_repository import SessionRepository


def test_query_and_history_survive_a_restart(tmp_path):
    path = tmp_path / "sessions.db"
    first = SessionRepository(path)
    first.create_query("q1", "s1", "上个月门诊人次")
    first.append_turn("s1", "user", "上个月门诊人次")
    first.append_turn("s1", "assistant", "需要补充信息")
    first.close()

    reopened = SessionRepository(path)

    record = reopened.get_query("q1")
    assert record is not None
    assert record.session_id == "s1"
    assert record.status == "pending"
    session = reopened.get("s1")
    assert session is not None
    assert session.query_ids == ["q1"]
    assert [turn["role"] for turn in session.history] == ["user", "assistant"]


def test_status_update_is_visible_to_the_next_process(tmp_path):
    path = tmp_path / "sessions.db"
    first = SessionRepository(path)
    first.create_query("q1", "s1", "问题")
    first.update_query("q1", "ok", "SELECT 1")
    first.close()

    assert SessionRepository(path).get_query("q1").status == "ok"
    assert SessionRepository(path).get_query("q1").sql == "SELECT 1"


def test_prune_returns_the_expired_query_ids(tmp_path):
    repository = SessionRepository(tmp_path / "sessions.db", ttl_hours=1)
    repository.create_query("old", "s1", "旧问题")
    repository.create_query("fresh", "s1", "新问题")
    repository.append_turn("s1", "user", "旧消息")

    two_hours_ago = time.time() - 2 * 3600
    repository.conn.execute("UPDATE queries SET updated_at = ? WHERE query_id = 'old'", (two_hours_ago,))
    repository.conn.execute("UPDATE turns SET created_at = ?", (two_hours_ago,))
    repository.conn.commit()

    assert repository.prune() == ["old"]
    assert repository.get_query("old") is None
    assert repository.get_query("fresh") is not None
    # The expired turn is gone too, but the session itself stays while fresh
    # rows still point at it.
    assert repository.get("s1").history == []


def test_history_is_capped_oldest_first(tmp_path):
    repository = SessionRepository(tmp_path / "sessions.db")
    for index in range(60):
        repository.append_turn("s1", "user", f"m{index}")

    history = repository.get("s1").history
    assert len(history) == 50
    assert history[-1]["content"] == "m59"
