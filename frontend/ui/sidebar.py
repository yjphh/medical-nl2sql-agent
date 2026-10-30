from __future__ import annotations

import time
from html import escape
from typing import Any

import streamlit as st

from frontend.api_client import BackendClient, BackendError
from frontend.models import Metric
from frontend.state import MODES, jump_to, on_mode_change, reset_conversation

HEALTH_TTL_SECONDS = 5.0

# The history is meant to read like a document outline (Feishu-style): a flat,
# numbered list, no button chrome, the entry you are on in blue.  Streamlit
# tags every keyed widget with ``st-key-<key>``, which is what we target.
HISTORY_CSS = """
<style>
.hist-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.95rem; font-weight: 700;
}
.hist-count {
  padding: 0 7px; border-radius: 9px; line-height: 1.6;
  font-size: 0.7rem; font-weight: 600;
  color: #3370ff; background: rgba(51, 112, 255, 0.1);
}
.hist-mode {
  margin: 2px 0 4px; font-size: 0.76rem; opacity: 0.65;
  color: var(--text-color, inherit);
}
.hist-sub {
  margin: -8px 0 0 26px; font-size: 0.76rem; opacity: 0.65;
  color: var(--text-color, inherit);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
/* The outline column (Feishu's middle pane): stays put while the chat scrolls. */
.st-key-hist_panel {
  position: sticky; top: 1rem;
  max-height: calc(100vh - 9rem); overflow-y: auto;
  padding: 2px 14px 8px 0;
}
/* Full-height divider between the outline and the chat, like Feishu's panes. */
div[data-testid="stColumn"]:has(.st-key-hist_panel) {
  border-right: 1px solid rgba(128, 128, 128, 0.18);
  padding-right: 1rem;
}
[class*="st-key-jump_"] { margin-bottom: -8px; }
[class*="st-key-jump_"] button {
  width: 100% !important; min-height: 0; padding: 3px 8px;
  justify-content: flex-start; text-align: left;
  background: transparent; border: none; box-shadow: none;
}
/* Streamlit centres the label span inside the button; left-align it so every
   entry starts at the same x, like a document outline. */
[class*="st-key-jump_"] button > div {
  width: 100%; justify-content: flex-start !important; text-align: left !important;
}
[class*="st-key-jump_"] button > div span,
[class*="st-key-jump_"] button [data-testid="stMarkdownContainer"] {
  width: 100%; justify-content: flex-start !important; text-align: left !important;
}
[class*="st-key-jump_"] button p {
  font-size: 0.86rem; line-height: 1.55;
  color: var(--text-color, inherit) !important;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
[class*="st-key-jump_"] button:hover {
  background: rgba(128, 128, 128, 0.12); border: none;
}
[class*="st-key-jump_"] button:focus:not(:active) { border-color: transparent; }
[class*="st-key-jump_on_"] button,
[class*="st-key-jump_on_"] button:hover { background: rgba(51, 112, 255, 0.08); }
[class*="st-key-jump_on_"] button p,
[class*="st-key-jump_on_"] button:hover p { color: #3370ff !important; font-weight: 600; }
</style>
"""


def _cached_health(client: BackendClient) -> dict:
    """Probe ``/health`` at most once per TTL instead of on every rerun.

    A dead backend costs a full connect timeout per rerun otherwise.
    """
    cached = st.session_state.get("_health_cache")
    now = time.monotonic()
    if cached and now - cached[0] < HEALTH_TTL_SECONDS:
        return cached[1]
    report = client.health_report()
    st.session_state["_health_cache"] = (now, report)
    return report


def _cached_metrics(client: BackendClient, query: str) -> list[Metric]:
    cached = st.session_state.get("_metrics_cache")
    if cached and cached[0] == query:
        return cached[1]
    metrics = client.list_metrics(q=query, top_k=50)
    st.session_state["_metrics_cache"] = (query, metrics)
    return metrics


def render(client: BackendClient, busy: bool) -> tuple[bool, str | None]:
    """Returns (backend_healthy, question_from_metric_picker)."""
    report = _cached_health(client)
    healthy = report.get("status") == "ok"

    with st.sidebar:
        st.title("🏥 医疗 NL2SQL")
        st.caption("自然语言 → SQL（只生成，不执行）")

        if healthy:
            st.success("后端已连接", icon="✅")
        else:
            st.error(report.get("message") or f"后端未连接：{client.base_url}", icon="⚠️")
            if report.get("status") == "unreachable":
                st.code(
                    ".venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000",
                    language="powershell",
                )
            if st.button("重新检测后端", use_container_width=True):
                st.session_state["_health_cache"] = None
                st.rerun()

        if st.session_state.session_id:
            st.caption(f"session：`{st.session_state.session_id[:8]}…`")

        if st.button("新建会话 / 清空对话", use_container_width=True):
            reset_conversation()
            st.session_state["_health_cache"] = None
            for key in list(st.query_params.keys()):
                del st.query_params[key]
            st.rerun()

        st.divider()
        st.radio(
            "模式",
            options=list(MODES),
            format_func=MODES.get,
            key="mode_pick",
            horizontal=True,
            on_change=on_mode_change,
            disabled=busy,
        )
        st.caption("智能引导：先澄清口径再生成；自由对话：直接让模型出 SQL。两种模式各自保存对话。")

        st.divider()
        metric_question = _metric_picker(client, healthy=healthy, busy=busy)
        if busy:
            st.caption("先回答对话中的澄清问题，再开始新的提问。")

        st.divider()
        st.toggle("显示调试 trace", key="show_trace")
        st.caption("trace 只用于验收排障，正常使用请关闭。")

    return healthy, metric_question


def render_history() -> None:
    """Middle column: every question in this mode, as a document outline.

    Clicking an entry scrolls the chat back to that question; the entry you are
    on (latest, or last jumped to) is highlighted the way Feishu marks the
    section currently in view.
    """
    entries = _question_entries(st.session_state.messages)
    mode_label = MODES.get(st.session_state.mode, "")
    st.markdown(HISTORY_CSS, unsafe_allow_html=True)
    # Sticky panel: the outline stays in place while the chat column scrolls.
    with st.container(key="hist_panel"):
        st.markdown(
            f"<div class='hist-title'>询问记录"
            f"<span class='hist-count'>{len(entries)}</span></div>"
            f"<div class='hist-mode'>{escape(mode_label)} · 点击条目跳回原问题</div>",
            unsafe_allow_html=True,
        )
        if not entries:
            st.caption("还没有提问。")
            return
        active_mid = _active_mid(entries)
        for index, entry in enumerate(entries, start=1):
            _render_entry(index, entry, active=entry["mid"] == active_mid)


def _question_entries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the transcript into questions, each followed by its answers.

    Answers become the indented sub-lines under an outline entry, so a
    clarified question reads as one headed section rather than loose bubbles.
    """
    entries: list[dict[str, Any]] = []
    for message in messages:
        kind = message.get("kind")
        if kind == "question":
            entries.append(
                {
                    "mid": message.get("mid"),
                    "content": str(message.get("content") or ""),
                    "answers": [],
                }
            )
        elif kind == "answer" and entries:
            entries[-1]["answers"].append(str(message.get("content") or ""))
    return entries


def _active_mid(entries: list[dict[str, Any]]) -> str | None:
    known = {entry["mid"] for entry in entries}
    active = st.session_state.get("active_mid")
    if active in known:
        return active
    return entries[-1]["mid"]


def _render_entry(index: int, entry: dict[str, Any], *, active: bool) -> None:
    mid = entry["mid"]
    # ``jump_on_`` is the highlighted variant; both keys start with ``jump_``.
    key = f"jump_on_{mid}" if active and mid else f"jump_{mid or index}"
    st.button(
        f"{index}. {_short(entry['content'], 24)}",
        key=key,
        on_click=jump_to,
        args=(mid,),
        use_container_width=True,
        disabled=not mid,
        help=entry["content"] or None,
    )
    for answer in entry["answers"]:
        st.markdown(
            f"<div class='hist-sub'>↳ {escape(_short(answer, 26))}</div>",
            unsafe_allow_html=True,
        )


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _metric_picker(
    client: BackendClient,
    *,
    healthy: bool,
    busy: bool,
) -> str | None:
    st.subheader("指标库")
    # A form keeps typing from firing one request per keystroke.
    with st.form("metric_search_form", border=False):
        search = st.text_input(
            "搜索指标",
            placeholder="门诊、费用、手术…",
            key="metric_search",
        )
        submitted = st.form_submit_button("搜索", use_container_width=True)

    metrics: list[Metric] = []
    if healthy:
        query = st.session_state.get("metric_query", "") if not submitted else search
        if submitted:
            st.session_state["metric_query"] = search
        try:
            metrics = _cached_metrics(client, query)
        except BackendError as exc:
            st.caption(f"指标加载失败：{exc.detail}")

    if not metrics:
        st.caption("没有匹配的指标。")
        return None

    labels = {f"{m.name}（{m.category}）" if m.category else m.name: m for m in metrics}
    choice = st.selectbox("选择指标", list(labels))
    metric = labels[choice]
    if metric.definition:
        st.caption(metric.definition)

    if st.button(
        "用这个指标提问",
        use_container_width=True,
        disabled=busy or not healthy,
    ):
        return metric.name
    return None
