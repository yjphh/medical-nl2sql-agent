"""Streamlit entry point: 医疗 NL2SQL 前端。

启动（仓库根目录）：
    .venv\\Scripts\\python.exe -m streamlit run frontend\\app.py

前端只调 FastAPI，不直连任何大模型；DeepSeek key 只放在后端的 .env 里。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st
from dotenv import load_dotenv

from frontend.api_client import BackendClient, BackendError
from frontend.models import QueryResponse
from frontend.state import add_message, init_state
from frontend.ui import chat, clarification, sidebar

load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(
    page_title="医疗 NL2SQL 智能查询助手",
    page_icon="🏥",
    layout="wide",
)

init_state()

BASE_URL = os.getenv("MEDICAL_API_BASE_URL", "http://127.0.0.1:8000")

EXAMPLES = [
    "2025年1月急诊人次",
    "上个月门诊人次是多少",
    "上个月心内科门诊患者的次均费用",
]


@st.cache_resource
def get_client(base_url: str) -> BackendClient:
    return BackendClient(base_url=base_url)


client = get_client(BASE_URL)


def handle_response(response: QueryResponse) -> None:
    st.session_state.query_id = response.query_id

    if response.status == "needs_clarification":
        if response.clarification is None:
            add_message("assistant", "后端要求澄清但没有返回问题，请重试。", kind="error")
            st.session_state.pending_clarification = None
            st.session_state.query_id = None
            return
        st.session_state.pending_clarification = response.clarification
        add_message("assistant", response.message or "需要再确认一个信息，才能继续。")
        return

    st.session_state.pending_clarification = None
    st.session_state.query_id = None

    if response.status == "ok":
        st.session_state.last_sql = response.sql
        add_message(
            "assistant",
            response.message or "SQL 已生成。",
            kind="sql",
            sql=response.sql,
            assumptions=response.assumptions,
            history=response.clarification_history,
            trace=response.trace,
            query_id=response.query_id,
        )
        return

    detail = (response.error or {}).get("message") or response.message or "生成失败"
    add_message("assistant", detail, kind="error")


def submit_question(question: str) -> None:
    if not st.session_state.session_id:
        try:
            st.session_state.session_id = client.create_session()
        except BackendError as exc:
            add_message("assistant", f"创建会话失败：{exc.detail}", kind="error")
            return

    add_message("user", question)
    with st.spinner("正在分析问题并生成 SQL…"):
        try:
            response = client.ask(st.session_state.session_id, question)
        except BackendError as exc:
            add_message("assistant", f"请求失败：{exc.detail}", kind="error")
            return
    handle_response(response)


pending = st.session_state.pending_clarification
healthy, metric_question = sidebar.render(client, busy=pending is not None)

st.title("医疗数据自然语言查询与 SQL 生成系统")
st.caption(
    "输入示例：2025年1月门诊人次。信息不足时系统会逐轮追问，"
    "最后生成 SQL（本轮不执行、不返回查询结果）。"
)

if not healthy:
    st.warning("后端未连接，请先在另一个终端启动 FastAPI，然后刷新页面。")

if metric_question and pending is None and healthy:
    submit_question(metric_question)
    st.rerun()

chat.render_messages(st.session_state.messages, show_trace=st.session_state.show_trace)

if pending is not None:

    def on_answer(option_id: str | None, text: str | None, shown: str) -> None:
        add_message("user", shown)
        with st.spinner("正在处理回答…"):
            try:
                response = client.answer(
                    st.session_state.query_id,
                    question_id=pending.question_id,
                    option_id=option_id,
                    text=text,
                )
            except BackendError as exc:
                add_message("assistant", f"提交回答失败：{exc.detail}", kind="error")
                st.session_state.pending_clarification = None
                st.session_state.query_id = None
                st.rerun()
                return
        handle_response(response)
        st.rerun()

    clarification.render(pending, on_answer)

elif healthy:
    if not st.session_state.messages:
        st.markdown("**试试这些：**")
        columns = st.columns(len(EXAMPLES))
        for column, example in zip(columns, EXAMPLES, strict=False):
            if column.button(example, use_container_width=True):
                submit_question(example)
                st.rerun()

    prompt = st.chat_input("请输入医疗数据查询，例如：上个月门诊人次是多少")
    if prompt:
        submit_question(prompt)
        st.rerun()
