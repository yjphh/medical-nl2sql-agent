from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from frontend.models import Clarification

CODE_LABELS = {
    "time_range": "时间口径",
    "time_field_scope": "统计时间字段",
    "visit_type_scope": "就诊类型",
    "metric_scope": "指标口径",
    "patient_scope": "患者范围",
    "department_entity": "科室匹配",
    "aggregation": "统计方式",
    "insurance_scope": "医保范围",
}

# on_answer(option_id, free_text, label_shown_in_transcript)
AnswerHandler = Callable[[str | None, str | None, str], None]


def render(clarification: Clarification, on_answer: AnswerHandler) -> None:
    options = clarification.options
    if not options:
        st.warning("后端没有返回可选项，请稍后重试。")
        return

    with st.container(border=True):
        label = CODE_LABELS.get(clarification.code, clarification.code)
        st.caption(f"第 {clarification.round}/{clarification.max_rounds} 轮 · {label}")
        st.markdown(f"**{clarification.question}**")

        option_ids = [option.id for option in options]
        default_id = clarification.default_option_id
        index = option_ids.index(default_id) if default_id in option_ids else 0
        labels = [
            f"{option.label}（默认）" if option.id == default_id else option.label
            for option in options
        ]
        choice = st.radio(
            "请选择",
            labels,
            index=index,
            key=f"clar_{clarification.question_id}",
            label_visibility="collapsed",
        )
        chosen = options[labels.index(choice)]

        free_text = ""
        if clarification.allow_free_text:
            with st.expander("以上都不是？直接用一句话回答"):
                free_text = st.text_input(
                    "补充说明",
                    key=f"free_{clarification.question_id}",
                    label_visibility="collapsed",
                )

        col_submit, col_skip, _ = st.columns([1, 1.4, 2])
        submit = col_submit.button(
            "确认",
            type="primary",
            key=f"submit_{clarification.question_id}",
        )
        skip = col_skip.button(
            "跳过，用默认值",
            key=f"skip_{clarification.question_id}",
            disabled=not clarification.can_skip,
        )

    if submit:
        text = free_text.strip()
        if text:
            on_answer(None, text, text)
        else:
            on_answer(chosen.id, None, chosen.label)
    elif skip:
        default_label = clarification.option_label(default_id) or "默认值"
        on_answer("skip", None, default_label)
