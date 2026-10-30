# Frontend（Streamlit）

医疗 NL2SQL 的对话界面：提问 → 逐轮澄清 → 生成 SQL（不执行）。

前端**只调用 FastAPI**（`frontend/api_client.py` 是唯一出口），不直连任何大模型；DeepSeek key 只在后端 `.env`。

## 启动

在**仓库根目录**开两个终端。

终端 1：后端

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

终端 2：前端

```powershell
.venv\Scripts\python.exe -m pip install -r frontend\requirements.txt
.venv\Scripts\python.exe -m streamlit run frontend\app.py
```

浏览器打开 `http://localhost:8501`。

后端地址通过 `frontend/.env` 覆盖（默认 `http://127.0.0.1:8000`）：

```text
MEDICAL_API_BASE_URL=http://127.0.0.1:8000
```

## 目录

```text
frontend/
├── app.py                  # 页面编排
├── api_client.py           # 唯一 HTTP 出口（可单测，不 import streamlit）
├── models.py               # 后端响应的 dataclass 视图
├── state.py                # st.session_state 初始化/重置
├── ui/
│   ├── sidebar.py          # 后端探活、会话、指标库、trace 开关
│   ├── chat.py             # 消息流
│   ├── clarification.py    # 澄清卡片：选项 / 默认值 / 跳过 / 自由文本 / 轮次
│   └── result.py           # SQL 卡片、口径说明、澄清记录、调试 trace
├── requirements.txt
└── .env.example
```

## 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\frontend -q
```

用 `httpx.MockTransport`，不联网、不需要启动后端。

## 手工验收清单

- [ ] 「2025年1月急诊人次」→ 直接出 SQL，页面写明"未执行"。
- [ ] 「上个月门诊人次是多少」→ 第 1/2 轮「自然月 / 最近30天」→ 第 2/2 轮「仅门诊 / 门诊+急诊」→ 出 SQL。
- [ ] 点「跳过，用默认值」→ 出 SQL，口径说明里标"按默认口径处理"。
- [ ] 「上个月外科住院患者的手术例数」→ 最多 3 轮，问题不重复。
- [ ] SQL 可复制、可下载 `.sql`。
- [ ] 后端停掉后刷新页面 → 顶部提示"后端未连接"，不白屏。
- [ ] 页面上没有 API Key 输入框，前端代码里没有 `openai`。

## 说明

- 刷新页面会丢失对话（`st.session_state` 仅存内存），这是 MVP 的已知限制。
- 本轮不执行 SQL、不展示结果表；"模拟执行"已移除。
- 同一 `query_id` 只能回答一次；回答后卡片消失，避免重复提交。
