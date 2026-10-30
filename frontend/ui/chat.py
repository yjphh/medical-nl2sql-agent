from __future__ import annotations

from typing import Any

import streamlit as st

from frontend.ui import result


def render_messages(messages: list[dict[str, Any]], show_trace: bool = False) -> None:
    for message in messages:
        _render_message(message, show_trace)


def _render_message(message: dict[str, Any], show_trace: bool) -> None:
    role = message.get("role", "assistant")
    with st.chat_message(role):
        if message.get("kind") == "error":
            st.error(message.get("content", "发生错误"))
            return
        if message.get("content"):
            st.markdown(message["content"])
        if message.get("sql"):
            result.render_sql_card(message, show_trace=show_trace)
