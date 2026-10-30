"""Streamlit session-state helpers."""

from __future__ import annotations

from typing import Any

import streamlit as st

DEFAULTS: dict[str, Any] = {
    "session_id": None,
    "messages": [],
    "query_id": None,
    "pending_clarification": None,
    "last_sql": None,
    "show_trace": False,
}


def init_state() -> None:
    for key, value in DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = list(value) if isinstance(value, list) else value


def reset_conversation() -> None:
    st.session_state.session_id = None
    st.session_state.messages = []
    st.session_state.query_id = None
    st.session_state.pending_clarification = None
    st.session_state.last_sql = None


def add_message(role: str, content: str, **extra: Any) -> None:
    message = {"role": role, "content": content}
    message.update(extra)
    st.session_state.messages.append(message)
