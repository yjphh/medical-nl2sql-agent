from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st

from frontend.sql_format import format_sql
from frontend.ui.clarification import CODE_LABELS


def render_sql_card(message: dict[str, Any], show_trace: bool = False) -> None:
    raw_sql = message.get("sql") or ""
    if not raw_sql:
        return

    question = message.get("question")
    if question:
        # Same wording as the chat bubble, so a scrolled-away question is still
        # visible next to the SQL it produced.
        st.markdown(
            "<div style='padding:6px 10px;margin-bottom:6px;border-left:3px solid #4c78a8;"
            "background:rgba(76,120,168,0.08);border-radius:4px'>"
            f"<b>问题：</b>{escape(str(question))}</div>",
            unsafe_allow_html=True,
        )
    if message.get("mode_label"):
        st.caption(message["mode_label"])

    sql = format_sql(raw_sql)
    st.code(sql, language="sql", line_numbers=True)
    if message.get("sql_result_empty"):
        st.warning(
            "SQL 可执行，但在本地镜像上命中 0 行。"
            "可能是时间范围、科室/类型过滤不对，或数据本身缺失——请核对后再使用。"
        )
    else:
        st.info("SQL 已生成，真实库未执行（已在本地 SQLite 镜像校验可执行性）。")

    query_id = message.get("query_id") or "query"
    st.download_button(
        "下载 .sql",
        data=sql,
        file_name=f"{query_id[:8]}.sql",
        mime="text/plain",
        key=f"download_{query_id}",
    )

    assumptions = message.get("assumptions") or []
    if assumptions:
        st.markdown("**口径说明**")
        for item in assumptions:
            if item.source == "default":
                st.markdown(f"- ⚠️ {item.label}（按默认口径处理）")
            elif item.code == "sql":
                st.markdown(f"- ℹ️ {item.label}（模型补充说明）")
            else:
                st.markdown(f"- ✅ {item.label}（您已确认）")

    history = message.get("history") or []
    if history:
        with st.expander(f"澄清记录（{len(history)} 轮）"):
            for turn in history:
                # Same Chinese labels as the clarification card, not the code.
                label = CODE_LABELS.get(str(turn.get("code")), turn.get("code"))
                st.markdown(
                    f"- 第 {turn.get('round')} 轮 · {label}：{turn.get('answer')}"
                )

    if show_trace and message.get("trace"):
        with st.expander("调试 trace"):
            st.json(message["trace"])
