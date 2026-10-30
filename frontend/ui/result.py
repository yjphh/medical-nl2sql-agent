from __future__ import annotations

from typing import Any

import streamlit as st


def render_sql_card(message: dict[str, Any], show_trace: bool = False) -> None:
    sql = message.get("sql") or ""
    if not sql:
        return

    st.code(sql, language="sql")
    st.info("SQL 已生成，未执行（本轮不连接数据库）。")

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
                st.markdown(
                    f"- 第 {turn.get('round')} 轮 · {turn.get('code')}："
                    f"{turn.get('answer')}"
                )

    if show_trace and message.get("trace"):
        with st.expander("调试 trace"):
            st.json(message["trace"])
