"""Streamlit session-state helpers."""

from __future__ import annotations

import uuid
from typing import Any

import streamlit as st

# The two conversations the UI can drive.  ``guided`` runs the clarification
# graph; ``direct`` asks the model once and only verifies on the mirror.
MODES: dict[str, str] = {"guided": "智能引导", "direct": "自由对话"}

# Keys that belong to one mode's conversation.  Switching modes snapshots these
# into ``mode_state`` and loads the other mode's copy back, so a pending
# clarification never leaks into the direct conversation.
MODE_KEYS: tuple[str, ...] = (
    "session_id",
    "messages",
    "query_id",
    "pending_clarification",
    "last_sql",
    "recover_query_id",
    "current_question",
    "active_mid",
)

DEFAULTS: dict[str, Any] = {
    "session_id": None,
    "messages": [],
    "query_id": None,
    "pending_clarification": None,
    "last_sql": None,
    "show_trace": False,
    # A request that timed out: the backend may still finish it, so keep the id
    # around and let the user fetch the result instead of re-asking.
    "recover_query_id": None,
    "mode": "guided",
    "mode_state": {},
    "scroll_target": None,
    "current_question": None,
    # Question recorded but not sent yet: asked on the next run, after its
    # bubble (and the "正在生成 SQL" line under it) is on screen.
    "pending_ask": None,
    # Question highlighted in the sidebar outline (the one being worked on).
    "active_mid": None,
    # Last mode written to the URL; lets app.py re-sync only after a switch.
    "_synced_mode": "guided",
}


def init_state() -> None:
    for key, value in DEFAULTS.items():
        if key not in st.session_state:
            if isinstance(value, list):
                st.session_state[key] = list(value)
            elif isinstance(value, dict):
                st.session_state[key] = dict(value)
            else:
                st.session_state[key] = value
    if "mode_pick" not in st.session_state:
        st.session_state["mode_pick"] = st.session_state.mode


def _snapshot() -> dict[str, Any]:
    return {key: st.session_state[key] for key in MODE_KEYS}


def _restore(snapshot: dict[str, Any] | None) -> None:
    for key in MODE_KEYS:
        value = (snapshot or {}).get(key)
        st.session_state[key] = list(value or []) if key == "messages" else value


def switch_mode(new_mode: str) -> None:
    old_mode = st.session_state.get("mode") or "guided"
    if new_mode == old_mode or new_mode not in MODES:
        return
    mode_state = dict(st.session_state.get("mode_state") or {})
    mode_state[old_mode] = _snapshot()
    _restore(mode_state.get(new_mode))
    st.session_state.mode_state = mode_state
    st.session_state.mode = new_mode
    st.session_state.mode_pick = new_mode
    st.session_state.scroll_target = None


def on_mode_change() -> None:
    """Radio callback: the widget already wrote ``mode_pick``."""
    switch_mode(st.session_state.get("mode_pick") or "guided")


def reset_conversation() -> None:
    """Clear the conversation of the *current* mode; the other one survives."""
    for key in MODE_KEYS:
        st.session_state[key] = [] if key == "messages" else None


def add_message(role: str, content: str, **extra: Any) -> dict[str, Any]:
    message = {"role": role, "content": content, "mid": uuid.uuid4().hex}
    message.update(extra)
    st.session_state.messages.append(message)
    return message


def last_question(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("kind") == "question":
            return str(message.get("content") or "")
    return None


def jump_to(mid: str | None) -> None:
    """Ask the chat area to scroll to one message (applied on the next run)."""
    if mid:
        st.session_state.scroll_target = mid
        # Same as a document outline: the entry you jump to becomes current.
        st.session_state.active_mid = mid
