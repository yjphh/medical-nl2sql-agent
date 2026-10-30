"""Streamlit entry point: 医疗 NL2SQL 前端。

启动（仓库根目录）：
    .venv\\Scripts\\python.exe -m streamlit run frontend\\app.py

前端只调 FastAPI，不直连任何大模型；DeepSeek key 只放在后端的 .env 里。
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from dotenv import load_dotenv

from frontend.api_client import BackendClient, BackendError
from frontend.models import QueryResponse
from frontend.state import add_message, init_state, last_question
from frontend.ui import chat, clarification, sidebar

load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(
    page_title="医疗 NL2SQL 智能查询助手",
    page_icon="🏥",
    layout="wide",
)

init_state()
st.markdown(chat.FLASH_CSS + chat.CHAT_CSS, unsafe_allow_html=True)

BASE_URL = os.getenv("MEDICAL_API_BASE_URL", "http://127.0.0.1:8000")
# One SQL generation is 1-3 LLM calls plus SQLite execution; 180s is plenty and
# still short enough to notice a hung backend.
TIMEOUT_SECONDS = float(os.getenv("MEDICAL_API_TIMEOUT", "180"))

EXAMPLES = [
    "2025年1月急诊人次",
    "上个月门诊人次是多少",
    "上个月心内科门诊患者的次均费用",
]


@st.cache_resource
def get_client(base_url: str, timeout: float) -> BackendClient:
    return BackendClient(base_url=base_url, timeout=timeout)


client = get_client(BASE_URL, TIMEOUT_SECONDS)


# --- URL state -------------------------------------------------------------
# A reload used to wipe the whole conversation.  The ids live in the query
# string so the pending clarification can be restored from the backend.


def _sync_url() -> None:
    params = st.query_params
    if st.session_state.session_id:
        params["session"] = st.session_state.session_id
    if st.session_state.query_id:
        params["query"] = st.session_state.query_id
    elif "query" in params:
        del params["query"]


def _clear_pending() -> None:
    st.session_state.pending_clarification = None
    st.session_state.query_id = None
    _sync_url()


# --- Backend calls ---------------------------------------------------------


def _with_progress(label: str, call: Callable[[], Any]) -> Any:
    """Run a blocking call while showing elapsed seconds.

    Streamlit executes the script in a single thread, so a "cancel" button
    could not fire mid-call; showing progress and keeping the request id for
    later recovery is what actually helps the user.
    """
    placeholder = st.empty()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call)
        # Draw the status line once up front (so it is on screen even for a
        # fast answer) and keep the transcript pinned below it.
        placeholder.markdown(f"⏳ {label}… 已用 0 秒")
        chat.scroll_to_bottom()
        while not future.done():
            elapsed = int(time.monotonic() - started)
            placeholder.markdown(f"⏳ {label}… 已用 {elapsed} 秒")
            time.sleep(0.25)
    placeholder.empty()
    return future.result()


def _report_failure(exc: BackendError, query_id: str | None = None) -> None:
    if exc.kind in {"timeout", "connect"} and query_id:
        # The backend may still be working on it: remember the id so the user
        # can fetch the result instead of starting over.
        st.session_state.recover_query_id = query_id
    if exc.kind == "timeout":
        hint = "等待后端超时，但请求可能仍在处理。稍后点「取回上次结果」，不必重新提问。"
    elif exc.kind == "connect":
        hint = "无法连接后端。请确认 FastAPI 已启动，再点侧边栏的「重新检测后端」。"
    else:
        hint = f"请求失败：{exc.detail}"
    add_message("assistant", hint, kind="error")


def _recover(query_id: str) -> None:
    """Fetch a query that was created but whose response never arrived."""
    try:
        response = client.get_query(query_id)
    except BackendError as exc:
        if exc.status_code == 404:
            st.session_state.recover_query_id = None
            add_message(
                "assistant",
                "后端没有这次查询（可能重启过），请重新提问。",
                kind="error",
            )
            return
        add_message("assistant", f"取回失败：{exc.detail}", kind="error")
        return
    st.session_state.recover_query_id = None
    handle_response(response, recovered=True)


def _resync(query_id: str) -> bool:
    """Refetch server state after a failed submit.

    Returns True when the UI was updated (the caller can stop handling the
    error), False when the backend is unreachable and the card must stay put.
    """
    try:
        response = client.get_query(query_id)
    except BackendError as exc:
        if exc.status_code != 404:
            return False
        _clear_pending()
        st.session_state.recover_query_id = None
        add_message(
            "assistant",
            "后端已不认识这次查询（可能重启过），这次对话无法继续，请重新提问。",
            kind="error",
        )
        return True
    handle_response(response, recovered=True)
    return True


def handle_response(response: QueryResponse, *, recovered: bool = False) -> None:
    st.session_state.query_id = response.query_id

    if response.status == "needs_clarification":
        if response.clarification is None:
            add_message("assistant", "后端要求澄清但没有返回问题，请重试。", kind="error")
            _clear_pending()
            return
        st.session_state.pending_clarification = response.clarification
        _sync_url()
        text = response.message or "需要再确认一个信息，才能继续。"
        if recovered:
            text = "已从后端恢复这次提问，请继续回答下面的问题。"
        add_message("assistant", text)
        return

    _clear_pending()

    if response.status == "ok":
        st.session_state.last_sql = response.sql
        # The card repeats the question so it stays readable after scrolling;
        # recovered responses have no ``current_question``, so fall back to the
        # last question bubble in this mode's transcript.
        question = st.session_state.get("current_question") or last_question(
            st.session_state.messages
        )
        mode = st.session_state.mode
        add_message(
            "assistant",
            response.message or "SQL 已生成。",
            kind="sql",
            sql=response.sql,
            question=question,
            mode=mode,
            mode_label=(
                "智能引导 · 澄清后的完整问题生成"
                if mode == "guided"
                else "自由对话 · 单次模型调用生成"
            ),
            sql_result_empty=response.sql_result_empty,
            sql_confidence=response.sql_confidence,
            assumptions=response.assumptions,
            history=response.clarification_history,
            trace=response.trace,
            query_id=response.query_id,
        )
        return

    error = response.error or {}
    detail = error.get("message") or response.message or "生成失败"
    add_message("assistant", detail, kind="error", technical=error.get("detail"))


def queue_question(question: str) -> None:
    """Record the question now; the request is sent on the next run.

    The transcript is rendered earlier in the script than the composer, so
    asking here would block before the user's own bubble is drawn: they would
    stare at a spinner without seeing what they asked.  Deferring by one run
    puts the bubble (and "正在生成 SQL" under it) on screen first.
    """
    st.session_state.recover_query_id = None
    if not st.session_state.session_id:
        try:
            st.session_state.session_id = client.create_session()
        except BackendError as exc:
            add_message("assistant", f"创建会话失败：{exc.detail}", kind="error")
            return
        _sync_url()

    asked = add_message("user", question, kind="question")
    st.session_state.current_question = question
    # Newest question is the one the outline highlights.
    st.session_state.active_mid = asked["mid"]
    st.session_state.pending_ask = question


def _ask_backend(question: str) -> None:
    # Client-chosen id: if this call times out we can still fetch the result.
    request_id = str(uuid.uuid4())
    # Captured here on purpose: ``st.session_state`` is thread-local, so the
    # worker thread cannot read it.
    session_id = st.session_state.session_id
    mode = st.session_state.mode
    try:
        response = _with_progress(
            "正在生成 SQL",
            lambda: client.ask(session_id, question, request_id=request_id, mode=mode),
        )
    except BackendError as exc:
        _report_failure(exc, request_id)
        return
    handle_response(response)


def _restore_from_url() -> None:
    """Resume a pending clarification after a page reload."""
    if st.session_state.pending_clarification is not None:
        return
    session_id = st.query_params.get("session")
    if session_id and not st.session_state.session_id:
        st.session_state.session_id = session_id
    query_id = st.query_params.get("query")
    if not query_id:
        return
    try:
        response = client.get_query(query_id)
    except BackendError:
        return
    if response.status != "needs_clarification":
        # Finished or failed queries are not replayed on reload.
        return
    handle_response(response, recovered=True)


pending = st.session_state.pending_clarification
healthy, metric_question = sidebar.render(client, busy=pending is not None)

# A mode switch loads another conversation; the URL must point at that one.
if st.session_state.get("_synced_mode") != st.session_state.mode:
    st.session_state["_synced_mode"] = st.session_state.mode
    _sync_url()

# Three panes, Feishu-document style: sidebar (navigation) | outline | chat.
outline_col, chat_col = st.columns([1, 3.4], gap="large")

with outline_col:
    sidebar.render_history()

with chat_col:
    st.title("医疗数据自然语言查询与 SQL 生成系统")
    st.caption(
        "输入示例：2025年1月门诊人次。信息不足时系统会逐轮追问，"
        "最后生成 SQL（不连接真实库，仅在本地镜像校验可执行性，不返回查询结果）。"
    )

    # Transcript pane with its own scrollbar, so the composer below never
    # scrolls out of view.
    with st.container(key="chat_scroll"):
        if healthy:
            _restore_from_url()
            pending = st.session_state.pending_clarification

        if not healthy:
            st.warning("后端未连接。启动 FastAPI 后点侧边栏的「重新检测后端」，不必刷新页面。")

        if metric_question and pending is None and healthy:
            queue_question(metric_question)
            st.rerun()

        count = len(st.session_state.messages)
        grew = st.session_state.get("_rendered_count") != count
        st.session_state["_rendered_count"] = count
        chat.render_messages(st.session_state.messages, show_trace=st.session_state.show_trace)
        # A click in the 询问记录 outline wins; otherwise keep new messages in view.
        chat.apply_scroll(st.session_state.scroll_target, to_bottom=grew)
        st.session_state.scroll_target = None

        # The bubble is on screen now, so ask; the progress line ("正在生成
        # SQL… 已用 N 秒") stays under it until the response arrives.
        queued = st.session_state.pending_ask
        if queued is not None and pending is None and healthy:
            st.session_state.pending_ask = None
            _ask_backend(queued)
            st.rerun()

        if st.session_state.recover_query_id:
            col_recover, _ = st.columns([1.2, 3])
            if col_recover.button("取回上次结果", use_container_width=True):
                _recover(st.session_state.recover_query_id)
                st.rerun()

        if pending is not None:

            def on_answer(option_id: str | None, text: str | None, shown: str) -> None:
                # Marked as an answer so the outline nests it under its question.
                add_message("user", shown, kind="answer")
                query_id = st.session_state.query_id
                try:
                    response = _with_progress(
                        "正在处理回答",
                        lambda: client.answer(
                            query_id,
                            question_id=pending.question_id,
                            option_id=option_id,
                            text=text,
                        ),
                    )
                except BackendError as exc:
                    # A failed submit must not throw away the user's clarification
                    # rounds: try to resync, otherwise keep the card for a retry.
                    if _resync(query_id):
                        st.rerun()
                        return
                    add_message(
                        "assistant",
                        f"提交回答失败：{exc.detail}。可直接再点「确认」重试。",
                        kind="error",
                    )
                    st.rerun()
                    return
                handle_response(response)
                st.rerun()

            clarification.render(pending, on_answer)
            if st.button("取消本次提问", key="cancel_pending"):
                _clear_pending()
                add_message("assistant", "已取消这次提问，之前的对话保留。")
                st.rerun()

        elif healthy and not st.session_state.messages:
            st.markdown("**试试这些：**")
            columns = st.columns(len(EXAMPLES))
            for column, example in zip(columns, EXAMPLES, strict=False):
                if column.button(example, use_container_width=True):
                    queue_question(example)
                    st.rerun()

    if pending is None and healthy:
        prompt = st.chat_input("请输入医疗数据查询，例如：上个月门诊人次是多少")
        if prompt:
            queue_question(prompt)
            st.rerun()

    # Size the transcript pane to the leftover viewport height.  The page
    # itself does not scroll; each pane scrolls on its own.
    chat.fit_chat_pane()
