"""A pending clarification must survive the backend restarting.

The old MemorySaver kept the graph state only in the process, so a client that
still held ``query_id`` got 404 after a restart and had to redo every round.
"""

from __future__ import annotations

import pytest
from langgraph.types import Command

from app.api import runtime
from app.config import settings
from app.graph.builder import build_graph
from app.graph.deps import GraphDeps
from app.memory import checkpointer
from tests.fakes import (
    FakeExecutor,
    FakeKnowledgeProvider,
    FakeLLMClient,
    FakeSQLAgentLLM,
)

AMBIGUITY = {
    "code": "department_entity",
    "category": "entity",
    "blocking": True,
    "priority": 60,
    "matched_term": "心内科",
    "question": "科室名称是精确匹配还是名称包含？",
    "options": ["精确匹配", "名称包含"],
    "default": "名称包含",
}


def make_graph():
    deps = GraphDeps(
        knowledge=FakeKnowledgeProvider(ambiguities=[AMBIGUITY]),
        llm=FakeLLMClient(),
        settings=settings,
        sql_llm=FakeSQLAgentLLM([], default_batch=[{"sql": "SELECT 1"}]),
        sql_executor=FakeExecutor(),
    )
    return build_graph(deps)


@pytest.fixture
def sqlite_checkpointer(tmp_path, monkeypatch):
    """Point the singleton at a temp file and clean up the caches afterwards."""
    monkeypatch.setattr(settings, "checkpoint_db_path", str(tmp_path / "checkpoints.db"))
    _forget_singletons()
    yield
    _forget_singletons()


def _forget_singletons():
    checkpointer.reset()
    runtime.reset()


def test_pending_clarification_is_resumed_after_a_restart(sqlite_checkpointer):
    config = {"configurable": {"thread_id": "restart-1"}}

    first = make_graph().invoke({"original_question": "心内科人次"}, config=config)
    question = first["__interrupt__"][0].value
    assert question["code"] == "department_entity"

    # Everything in process memory is gone; only the file on disk remains.
    _forget_singletons()

    options = question["options"]
    resumed = make_graph().invoke(
        Command(resume={"question_id": question["id"], "option_id": options[0]["id"]}),
        config=config,
    )

    assert resumed["status"] == "ok"
    assert resumed["sql"] == "SELECT 1"
    # The first round is still in the history, so the resume really continued
    # the same thread instead of starting over.
    assert len(resumed["clarification_history"]) == 1


def test_drop_thread_makes_the_state_unreadable(sqlite_checkpointer):
    config = {"configurable": {"thread_id": "gone-1"}}
    graph = make_graph()
    graph.invoke({"original_question": "心内科人次"}, config=config)

    checkpointer.drop_thread("gone-1")

    snapshot = graph.get_state(config)
    assert not snapshot.values


def test_prune_removes_the_checkpoint_of_an_expired_query(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "checkpoint_db_path", str(tmp_path / "checkpoints.db"))
    _forget_singletons()
    try:
        from app.main import prune_expired
        from app.memory.session_repository import SessionRepository

        repository = SessionRepository(tmp_path / "sessions.db", ttl_hours=1)
        # ``prune_expired`` imports the module attribute at call time, so this
        # keeps the test off the real ``data/sessions.db``.
        monkeypatch.setattr("app.memory.session_repository.repository", repository)

        config = {"configurable": {"thread_id": "expired-1"}}
        graph = make_graph()
        graph.invoke({"original_question": "心内科人次"}, config=config)
        repository.create_query("expired-1", "s1", "心内科人次")
        repository.conn.execute(
            "UPDATE queries SET updated_at = 0 WHERE query_id = 'expired-1'"
        )
        repository.conn.commit()

        assert prune_expired() == 1
        assert repository.get_query("expired-1") is None
        assert not graph.get_state(config).values
    finally:
        _forget_singletons()
