from __future__ import annotations

import streamlit as st

from frontend.api_client import BackendClient, BackendError
from frontend.state import reset_conversation


def render(client: BackendClient, busy: bool) -> tuple[bool, str | None]:
    """Returns (backend_healthy, question_from_metric_picker)."""
    healthy = client.health()

    with st.sidebar:
        st.title("🏥 医疗 NL2SQL")
        st.caption("自然语言 → SQL（只生成，不执行）")

        if healthy:
            st.success("后端已连接", icon="✅")
        else:
            st.error(f"后端未连接：{client.base_url}", icon="⚠️")
            st.code(
                ".venv\\Scripts\\python.exe -m uvicorn app.main:app --reload --port 8000",
                language="powershell",
            )

        if st.session_state.session_id:
            st.caption(f"session：`{st.session_state.session_id[:8]}…`")

        if st.button("新建会话 / 清空对话", use_container_width=True):
            reset_conversation()
            st.rerun()

        st.divider()
        metric_question = _metric_picker(client, healthy=healthy, busy=busy)
        if busy:
            st.caption("先回答左侧对话中的澄清问题，再开始新的提问。")

        st.divider()
        st.toggle("显示调试 trace", key="show_trace")
        st.caption("trace 只用于验收排障，正常使用请关闭。")

    return healthy, metric_question


def _metric_picker(
    client: BackendClient,
    *,
    healthy: bool,
    busy: bool,
) -> str | None:
    st.subheader("指标库")
    search = st.text_input(
        "搜索指标",
        placeholder="门诊、费用、手术…",
        key="metric_search",
    )

    metrics = []
    if healthy:
        try:
            metrics = client.list_metrics(q=search, top_k=50)
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
