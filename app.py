import os
import pandas as pd
import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="医疗 NL2SQL 智能查询助手",
    page_icon="🏥",
    layout="wide"
)

# ========== 初始化状态 ==========
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_sql" not in st.session_state:
    st.session_state.last_sql = ""
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "last_selected_metric" not in st.session_state:
    st.session_state.last_selected_metric = "不选择"


def clear_chat():
    st.session_state.messages = []
    st.session_state.last_sql = ""
    st.session_state.last_result = None
    st.session_state.last_selected_metric = "不选择"
    st.session_state.metric_select = "不选择"


# ========== 加载指标库 ==========
@st.cache_data
def load_metrics():
    path = "data/大屏指标校验脚本(2).xlsx"
    if os.path.exists(path):
        df = pd.read_excel(path, sheet_name=0)
        df = df.dropna(subset=["指标名称"])
        return df
    else:
        # 如果 Excel 没放对位置，先用示例数据让 UI 能跑
        return pd.DataFrame([
            {
                "分类": "门急诊情况",
                "指标名称": "门诊人次",
                "校验sql": "select sum(indicatorValue) from (...)",
                "备注": "演示 SQL"
            },
            {
                "分类": "门急诊情况",
                "指标名称": "急诊人次",
                "校验sql": "select sum(indicatorValue) from (...)",
                "备注": "演示 SQL"
            },
        ])


metrics_df = load_metrics()
metric_names = metrics_df["指标名称"].dropna().astype(str).unique().tolist()

# ========== 侧边栏 ==========
with st.sidebar:
    st.title("🏥 医疗 NL2SQL")
    st.caption("自然语言 → SQL 查询助手")

    api_key = st.text_input(
        "DeepSeek API Key",
        type="password",
        value=os.getenv("DEEPSEEK_API_KEY", "")
    )
    base_url = st.text_input(
        "Base URL",
        value=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    model = st.text_input(
        "模型",
        value=os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    )

    st.divider()
    st.subheader("指标库")
    selected_metric = st.selectbox(
        "快速选择指标",
        ["不选择"] + metric_names,
        key="metric_select"
    )

    if selected_metric == "不选择":
        st.session_state.last_selected_metric = "不选择"
    elif selected_metric != st.session_state.last_selected_metric:
        row = metrics_df[metrics_df["指标名称"].astype(str) == selected_metric].iloc[0]
        st.session_state.messages.append({
            "role": "user",
            "content": f"查询指标：{selected_metric}"
        })
        st.session_state.messages.append({
            "role": "assistant",
            "content": f"已匹配到指标 **{selected_metric}**，SQL 如下：",
            "sql": str(row["校验sql"])
        })
        st.session_state.last_sql = str(row["校验sql"])
        st.session_state.last_selected_metric = selected_metric
        st.rerun()

    if st.button("清空对话", on_click=clear_chat):
        pass

# ========== 主界面 ==========
st.title("医疗数据自然语言查询与 SQL 生成系统")
st.caption("输入示例：查询 2025 年门诊人次；系统会优先匹配指标库，匹配不到再调用 AI 生成 SQL。")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sql"):
            st.code(msg["sql"], language="sql")


# ========== 工具函数 ==========
def match_metric(text: str, df: pd.DataFrame):
    text = text.strip()
    df_sorted = df.copy()
    df_sorted["名称长度"] = df_sorted["指标名称"].astype(str).str.len()
    df_sorted = df_sorted.sort_values("名称长度", ascending=False)
    for _, row in df_sorted.iterrows():
        name = str(row["指标名称"]).strip()
        if name and name in text:
            return row
    return None


def call_llm(prompt: str, api_key: str, base_url: str, model: str):
    if not api_key:
        return None
    client = OpenAI(api_key=api_key, base_url=base_url)
    system_prompt = """
你是医疗 NL2SQL 助手。请根据用户问题生成一条只读 SQL。
要求：
1. 只输出 SQL，不要解释。
2. 只能使用 SELECT，禁止 UPDATE、DELETE、DROP、INSERT。
3. 如果信息不足，输出：NEED_CLARIFY: 需要补充的问题。
"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            timeout=30
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"ERROR: {e}"


def mock_execute(sql: str):
    return pd.DataFrame({
        "指标": ["门诊人次"],
        "日期": ["2025-01-01", "2025-01-02", "2025-01-03"],
        "值": [1234, 1356, 1420]
    })


# ========== 聊天输入 ==========
if prompt := st.chat_input("请输入医疗数据查询，例如：2025 年门诊人次"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    row = match_metric(prompt, metrics_df)

    if row is not None:
        sql = str(row["校验sql"])
        answer = f"匹配到指标 **{row['指标名称']}**。"
        st.session_state.messages.append({
            "role": "assistant",
            "content": answer,
            "sql": sql
        })
        st.session_state.last_sql = sql
    else:
        if "年" not in prompt and "月" not in prompt and "时间" not in prompt:
            answer = "请补充时间范围，例如：2025 年 1 月到 2025 年 12 月。"
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer
            })
        else:
            sql = call_llm(prompt, api_key, base_url, model)
            if sql and sql.startswith("ERROR"):
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"AI 调用失败：{sql}"
                })
            elif sql and sql.startswith("NEED_CLARIFY"):
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": sql.replace("NEED_CLARIFY:", "需要澄清：")
                })
            elif sql:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "AI 生成的 SQL：",
                    "sql": sql
                })
                st.session_state.last_sql = sql
            else:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": "未匹配到指标，且未配置 API Key。请配置 API Key 或从侧边栏选择指标。"
                })

    st.rerun()

# ========== 当前 SQL 展示 ==========
if st.session_state.last_sql:
    st.divider()
    st.subheader("当前 SQL")
    st.code(st.session_state.last_sql, language="sql")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("模拟执行"):
            st.session_state.last_result = mock_execute(st.session_state.last_sql)
    with col2:
        if st.button("清空当前 SQL"):
            st.session_state.last_sql = ""
            st.session_state.last_result = None
            st.rerun()

if st.session_state.last_result is not None:
    st.subheader("模拟查询结果")
    st.dataframe(st.session_state.last_result, use_container_width=True)
