"""UI flow tests for ``frontend/app.py`` with a stubbed backend.

``AppTest`` runs the real script in-process, so patching ``BackendClient``
methods keeps these tests offline and backend-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from frontend.api_client import BackendClient, BackendError
from frontend.models import Clarification, Option, QueryResponse
from frontend.ui import chat

APP = str(Path(__file__).resolve().parents[2] / "frontend" / "app.py")


def _waiting_response() -> QueryResponse:
    return QueryResponse(
        session_id="s1",
        query_id="q1",
        status="needs_clarification",
        clarification=Clarification(
            question_id="q_time_range",
            code="time_range",
            question="您说的『上个月』是指自然月还是最近30天？",
            options=[
                Option(id="natural_month", label="自然月", value="自然月"),
                Option(id="last_30_days", label="最近30天", value="最近30天"),
            ],
            default_option_id="natural_month",
            round=1,
            max_rounds=2,
        ),
        message="需要补充信息后才能生成 SQL。",
    )


def _ok_response() -> QueryResponse:
    return QueryResponse(
        session_id="s1",
        query_id="q1",
        status="ok",
        sql="SELECT COUNT(*) AS visits FROM visit",
        message="SQL 已生成，真实库未执行（已在本地 SQLite 镜像校验可执行性）。",
        sql_result_empty=True,
    )


def _unreachable(self, query_id):
    raise BackendError(0, "无法连接后端", kind="connect")


@pytest.fixture
def app(monkeypatch):
    # Every method is stubbed: a backend actually running on :8000 must not
    # change the outcome of these tests.
    monkeypatch.setattr(
        BackendClient, "health_report", lambda self, timeout=2.0: {"status": "ok"}
    )
    monkeypatch.setattr(BackendClient, "health", lambda self, timeout=2.0: True)
    monkeypatch.setattr(BackendClient, "create_session", lambda self, user_id=None: "s1")
    monkeypatch.setattr(
        BackendClient,
        "ask",
        lambda self, session_id, question, request_id=None, mode="guided": _waiting_response(),
    )
    monkeypatch.setattr(BackendClient, "get_query", _unreachable)
    at = AppTest.from_file(APP, default_timeout=15)
    at.run()
    return at


def _ask(at: AppTest) -> AppTest:
    at.chat_input[0].set_value("上个月门诊人次是多少").run()
    return at


def _button(at: AppTest, key: str):
    return next(button for button in at.button if button.key == key)


def _stub_ok(monkeypatch):
    monkeypatch.setattr(
        BackendClient,
        "ask",
        lambda self, session_id, question, request_id=None, mode="guided": _ok_response(),
    )


def test_direct_sql_shows_empty_result_warning(app, monkeypatch):
    _stub_ok(monkeypatch)
    at = _ask(app)

    assert at.session_state["pending_clarification"] is None
    warnings = [w.value for w in at.warning]
    assert any("0 行" in text for text in warnings)


def test_failed_answer_keeps_the_clarification_card(app, monkeypatch):
    """The user's rounds are not thrown away when the backend hiccups."""
    at = _ask(app)
    assert at.session_state["pending_clarification"] is not None

    def boom(self, query_id, **kwargs):
        raise BackendError(0, "无法连接后端", kind="connect")

    monkeypatch.setattr(BackendClient, "answer", boom)
    _button(at, "submit_q_time_range").click().run()

    assert at.session_state["pending_clarification"] is not None
    assert at.session_state["query_id"] == "q1"
    assert any("重试" in err.value for err in at.error)


def test_conflict_is_resynced_from_the_backend(app, monkeypatch):
    """404/409 means the server moved on: fetch its state instead of dropping."""
    at = _ask(app)

    def conflict(self, query_id, **kwargs):
        raise BackendError(409, "query is not waiting for an answer", kind="http")

    monkeypatch.setattr(BackendClient, "answer", conflict)
    monkeypatch.setattr(BackendClient, "get_query", lambda self, query_id: _ok_response())
    _button(at, "submit_q_time_range").click().run()

    assert at.session_state["pending_clarification"] is None
    # SQL is printed multi-line now, so compare with whitespace collapsed.
    codes = [" ".join(block.value.split()) for block in at.code]
    assert any("SELECT COUNT(*) AS visits" in code for code in codes)


def test_reload_restores_the_pending_clarification(app, monkeypatch):
    """A refresh must resume the parked question, not restart the query."""
    import streamlit

    monkeypatch.setattr(streamlit, "query_params", {"query": "q1", "session": "s1"})
    monkeypatch.setattr(BackendClient, "get_query", lambda self, query_id: _waiting_response())
    at = AppTest.from_file(APP, default_timeout=15)
    at.run()

    assert at.session_state["session_id"] == "s1"
    assert at.session_state["pending_clarification"] is not None
    assert at.session_state["query_id"] == "q1"
    assert any("恢复" in msg.value for msg in at.markdown)


def test_cancel_pending_keeps_history(app):
    at = _ask(app)
    _button(at, "cancel_pending").click().run()

    assert at.session_state["pending_clarification"] is None
    assert at.session_state["query_id"] is None
    assert len(at.session_state["messages"]) >= 2


def _radio(at: AppTest, key: str):
    return next(widget for widget in at.radio if widget.key == key)


def test_question_is_drawn_before_the_backend_is_asked(app, monkeypatch):
    """The user's bubble must already be in the transcript when the wait starts."""
    drawn: list[str] = []

    original = chat.render_messages

    def spy(messages, show_trace=False):
        drawn.extend(str(message.get("content") or "") for message in messages)
        original(messages, show_trace=show_trace)

    monkeypatch.setattr(chat, "render_messages", spy)

    def ask(self, session_id, question, request_id=None, mode="guided"):
        assert question in drawn
        return _ok_response()

    monkeypatch.setattr(BackendClient, "ask", ask)
    at = _ask(app)

    assert at.session_state["pending_ask"] is None
    assert any("上个月门诊人次是多少" in block.value for block in at.markdown)


def test_sql_card_repeats_the_question(app, monkeypatch):
    _stub_ok(monkeypatch)
    at = _ask(app)

    markdown = [m.value for m in at.markdown]
    assert any("问题：" in text and "上个月门诊人次是多少" in text for text in markdown)


def test_sql_is_printed_multi_line(app, monkeypatch):
    _stub_ok(monkeypatch)
    at = _ask(app)

    blocks = [block.value for block in at.code]
    assert any("\n" in block for block in blocks)


def test_question_history_jumps_to_the_question(app, monkeypatch):
    _stub_ok(monkeypatch)
    at = _ask(app)

    jumps = [button.key for button in at.button if (button.key or "").startswith("jump_")]
    assert len(jumps) == 1
    _button(at, jumps[0]).click().run()

    assert at.session_state["scroll_target"] is None
    assert not at.exception


def test_question_history_highlights_the_active_question(app, monkeypatch):
    """Outline style: only the question being worked on is marked current."""
    _stub_ok(monkeypatch)
    at = _ask(app)

    jumps = [button.key for button in at.button if (button.key or "").startswith("jump_")]
    assert jumps[0].startswith("jump_on_")

    at.chat_input[0].set_value("2025年1月急诊人次").run()
    jumps = [button.key for button in at.button if (button.key or "").startswith("jump_")]
    assert len(jumps) == 2
    assert sum(key.startswith("jump_on_") for key in jumps) == 1
    assert jumps[-1].startswith("jump_on_")


def test_question_history_nests_answers_under_the_question(app, monkeypatch):
    at = _ask(app)

    monkeypatch.setattr(
        BackendClient,
        "answer",
        lambda self, query_id, **kwargs: _ok_response(),
    )
    _button(at, "submit_q_time_range").click().run()

    markdown = [m.value for m in at.markdown]
    assert any("↳ 自然月" in text for text in markdown)


def test_modes_keep_separate_conversations(app, monkeypatch):
    _stub_ok(monkeypatch)
    at = _ask(app)
    guided_messages = len(at.session_state["messages"])
    assert guided_messages == 2

    _radio(at, "mode_pick").set_value("direct").run()
    assert at.session_state["mode"] == "direct"
    assert at.session_state["messages"] == []

    _ask(at)
    assert len(at.session_state["messages"]) == 2

    _radio(at, "mode_pick").set_value("guided").run()
    assert at.session_state["mode"] == "guided"
    assert len(at.session_state["messages"]) == guided_messages
