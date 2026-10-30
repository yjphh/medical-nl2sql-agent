# 前端开发计划：基于 Streamlit 原型的医疗 NL2SQL 界面

> 基线：`D:\02pythonLearn\medical-nl2sql-ui\medical-nl2sql-ui\app.py`（237 行 Streamlit 单文件原型）。
> 目标：把它接到本项目的 FastAPI 后端，变成"提问 → 多轮澄清 → 出 SQL"的完整界面，**前端不再直接调 DeepSeek**。
> 本轮仍未接数据库执行，前端只展示 SQL，不展示查询结果。

## 0. 实现状态（2026-10-30）

| 项 | 状态 |
|---|---|
| 后端 `GET /metrics` | ✅ `app/api/routes_knowledge.py`，注册在 `app/main.py`；`pytest tests -q` 覆盖 |
| 前端代码 | ✅ `frontend/`：`app.py`、`api_client.py`、`models.py`、`state.py`、`ui/{sidebar,chat,clarification,result}.py` |
| 依赖 | ✅ `streamlit 1.66.1` 已装进 `.venv`（`frontend/requirements.txt`） |
| 单测 | ✅ `tests/frontend/test_api_client.py` 8 项（`httpx.MockTransport`，不联网） |
| AppTest 冒烟 | ✅ 无后端时提示"未连接"不崩溃；有后端时「2025年1月急诊人次」直出 SQL、「上个月门诊人次是多少」两轮澄清出 SQL、"跳过"路径生成 assumptions |
| 真实启动 | ✅ uvicorn `:8000` + `streamlit run frontend/app.py` `:8501`，`/_stcore/health` 返回 ok |
| 全量测试 | ✅ `pytest tests -q` → 28 passed；`ruff check` 全绿 |
| 文档 | ✅ `frontend/README.md`、`docs/GETTING_STARTED.md` 第 3 节（启动）、`docs/API.md` |

实现与计划的差异：澄清卡片用 `st.container(border=True)` 就地渲染（不插入聊天流历史），回答后写入 `messages` 作为普通用户消息；其余与第 4 节一致。

## 1. 现状与差距

原型能跑，但它是"另一个系统"：

| 原型行为 | 问题 | 本项目要求 |
|---|---|---|
| 前端填 API Key，浏览器侧直连 DeepSeek | key 暴露在前端；绕过澄清工作流 | key 只在后端 `.env`；前端只调 FastAPI |
| 用 Excel 里的 `校验sql` 做关键词匹配，命中就直接展示 SQL | 没有意图识别、歧义检测、多轮澄清 | 一律走 `POST /queries` |
| 匹配不到就调一次大模型，`NEED_CLARIFY:` 是一句自由文本 | 没有结构化选项、没有轮次、没有 assumptions | 结构化 `clarification.options`、`round/max_rounds` |
| "模拟执行"返回写死的 DataFrame | 本轮不执行 SQL，会误导 | 删除；改为"SQL 未执行"提示 + 复制/下载 |
| 会话只存在 `st.session_state` | 刷新即丢，`thread_id` 概念缺失 | 保留 `session_id` + `query_id`，一题一 thread |
| 侧边栏指标库读本地 Excel | 与知识层重复，容易不一致 | 改为后端 `GET /metrics` |

原型可复用的部分：整体布局（侧边栏 + 聊天流 + SQL 代码块）、`st.chat_message` / `st.chat_input` 交互、指标库快捷选择的产品思路。

## 2. 技术选型

**继续用 Streamlit**（不换 React/Next），理由：

1. 原型就是 Streamlit，改造量最小，前端只有 1 人维护；
2. Streamlit 的请求发生在 Python 进程里，不经过浏览器，**不需要给 FastAPI 配 CORS**；
3. 多轮澄清是"一问一答"的表单式交互，Streamlit 的 `radio` + `button` 足够；
4. 后续要换 React 时，后端契约不变，只换渲染层。

代价：页面刷新会丢 `st.session_state`（MVP 接受）；不能做流式逐字输出（不需要）。

依赖只保留：`streamlit`、`httpx`、`python-dotenv`。**移除** `openai`、`pandas`、`openpyxl`（不再前端读 Excel、不再直连模型）。

## 3. 后端契约

### 3.1 已实现（直接可用）

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 启动时探活，后端没起就在侧边栏提示 |
| `POST` | `/sessions` | 建会话，拿 `session_id` |
| `POST` | `/queries` | 提问，返回 `ok / needs_clarification / error` |
| `POST` | `/queries/{query_id}/answers` | 回答澄清，`Command(resume=...)` |
| `GET` | `/queries/{query_id}` | 查状态 / 恢复页面 |

`QueryResponse` 字段：`status`、`sql`、`clarification{question_id, code, question, options[{id,label,value}], default_option_id, round, max_rounds, allow_free_text, can_skip}`、`assumptions[{code,label,value,source}]`、`clarification_history`、`message`、`error`、`trace`。

`answers` 的请求体：`{"answer": {"question_id": "...", "option_id": "..."}}`，自由文本用 `text`，跳过用 `option_id="skip"`。

### 3.2 需要补的后端接口（B，约 30 行）

**`GET /metrics?q=&top_k=50`** —— 侧边栏指标库。

```json
{
  "items": [
    {
      "id": "outpatient_visits",
      "name": "门诊人次",
      "category": "门急诊情况",
      "aliases": ["门诊量", "门诊人数"],
      "definition": "...",
      "unit": "人次"
    }
  ]
}
```

实现：`app/api/routes_knowledge.py`，直接读 `runtime` 里的 `deps.knowledge.bundle.metrics`（只读、不调 LLM）。前端不用 Excel，指标清单以知识层为准。

**不需要**：CORS、WebSocket、流式接口、SQL 执行接口。

## 4. 页面与交互

```
┌─────────────── 侧边栏 ───────────────┬──────────────── 主区 ────────────────┐
│ 🏥 医疗 NL2SQL                        │ 医疗数据自然语言查询与 SQL 生成        │
│ 后端状态 ● 已连接 / ● 未连接           │                                       │
│ session: 3f2a…        [新建会话]       │ 👤 上个月门诊人次是多少                │
│ ─────────────────────────             │ 🤖 需要补充 2 个信息                   │
│ 指标库（可搜索）                       │  ┌ 第 1/2 轮 · 时间口径 ────────────┐  │
│  [门诊人次        ▾]                  │  │ 您说的『上个月』是指自然月还是…？  │  │
│  分类：门急诊情况                      │  │ (•) 自然月   ( ) 最近30天          │  │
│  [填入输入框]                          │  │ [ 提交 ]  [ 跳过，用默认值 ]       │  │
│ ─────────────────────────             │  └───────────────────────────────────┘  │
│ [清空对话]                             │                                       │
│ ─────────────────────────             │ 👤 自然月                              │
│ ⚙ 高级：显示 trace                     │ ┌ 第 2/2 轮 · 就诊类型 ──────────────┐  │
│                                       │  │ (•) 仅门诊  ( ) 门诊+急诊          │  │
│                                       │  └───────────────────────────────────┘  │
│                                       │                                       │
│                                       │ 🤖 已生成 SQL（未执行）                │
│                                       │ ┌ sql ────────────────────────────┐   │
│                                       │ │ SELECT COUNT(*) …               │   │
│                                       │ └─────────────────────────────────┘   │
│                                       │ 口径假设：时间口径=自然月；就诊类型=…  │
│                                       │ [复制] [下载 .sql] [查看澄清记录 ▸]    │
└───────────────────────────────────────┴───────────────────────────────────────┘
```

关键交互规则：

1. **一次只显示一张澄清卡片**：后端每轮只返回一个问题；用户提交后卡片消失，下一张出现（或直接出 SQL）。
2. **选项必须可点**：`st.radio` + `horizontal`，标签用后端返回的 `label`（已经过润色），值用 `option_id`。
3. **默认值可见**：`default_option_id` 对应项前置 `（默认）`；"跳过"按钮等价于提交 `option_id="skip"`，提示"将按默认口径生成"。
4. **自由文本**：`text_input` 折叠在"以上都不是？"下，提交时传 `text`。
5. **轮次可见**：卡片标题显示 `第 {round}/{max_rounds} 轮 · {code 的中文名}`。
6. **不能重复提交**：提交后立即 `st.rerun()`，卡片只由 `pending_clarification` 渲染，回答后置空。
7. **SQL 卡片**：`st.code(sql, language="sql")` + `st.download_button` + `st.code` 自带复制；固定提示"SQL 已生成，未执行（本轮不连数据库）"。
8. **assumptions**：`source=default` 的项标黄"按默认口径处理"，用户回答的项标灰"您已确认"。
9. **错误**：`status=error` 显示 `error.message` + "重试"按钮（重发同一问题，新开 query）。
10. **trace**：默认隐藏，侧边栏开关打开后 `st.expander` 展示 `trace` 原始 JSON，方便验收/排障。

## 5. 目录结构（`frontend/`）

```text
frontend/
├── app.py                  # 页面编排（Streamlit 入口，约 250 行）
├── api_client.py           # 唯一 HTTP 出口：BackendClient
├── models.py               # dataclass：QueryResponse / Clarification / Assumption
├── state.py                # st.session_state 的初始化与重置
├── ui/
│   ├── sidebar.py          # 后端状态、会话、指标库、清空
│   ├── chat.py             # 消息流渲染
│   ├── clarification.py    # 澄清卡片（radio + 自由文本 + 跳过）
│   └── result.py           # SQL 卡片、assumptions、历史、trace
├── requirements.txt        # streamlit, httpx, python-dotenv
├── .env.example            # MEDICAL_API_BASE_URL=http://127.0.0.1:8000
└── README.md               # 启动方式 + 验收清单
```

`api_client.py` 不 import streamlit，保证可单测：

```python
class BackendClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None: ...
    def health(self) -> bool: ...
    def create_session(self, user_id: str | None = None) -> str: ...
    def list_metrics(self, q: str = "") -> list[Metric]: ...
    def ask(self, session_id: str, question: str) -> QueryResponse: ...
    def answer(self, query_id: str, *, question_id: str | None,
               option_id: str | None = None, text: str | None = None) -> QueryResponse: ...
    def get_query(self, query_id: str) -> dict: ...
```

所有方法在 HTTP 失败时抛 `BackendError(status, detail)`，由 UI 层转成友好提示，**不吞异常**。

## 6. 状态模型（`state.py`）

```python
st.session_state.session_id: str | None      # 首次提问时自动创建
st.session_state.messages: list[dict]        # {role, kind: text|sql|clarification, content, sql?, assumptions?}
st.session_state.query_id: str | None        # 当前进行中的查询
st.session_state.pending_clarification: dict | None
st.session_state.last_sql: str | None
st.session_state.show_trace: bool
st.session_state.selected_metric_id: str | None
```

规则：`query_id` 只在"正在等回答"时有值；收到 `ok`/`error` 后清空，下一次提问重新 `POST /queries`。

## 7. 分阶段实施

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| P0 | 后端补 `GET /metrics`；`api_client.py` + `models.py`；会话创建 + 提问 + 纯文本回答 | 能在页面问一句、看到 SQL | 0.5 天 |
| P1 | 澄清卡片（选项/默认/跳过/自由文本/轮次）；SQL 卡片 + assumptions + 澄清历史 | 完整三轮对话可走通 | 1 天 |
| P2 | 侧边栏指标库、后端探活、错误重试、清空会话、trace 开关；去掉"模拟执行" | 可用界面 | 0.5 天 |
| P3 | `pytest`（`httpx.MockTransport` 测 api_client）、README、`docs/GETTING_STARTED.md` 增补 | 可交接 | 0.5 天 |

## 8. 验收标准

- [ ] 页面上输入「2025年1月急诊人次」→ 直接出 SQL，页面明确写"未执行"。
- [ ] 输入「上个月门诊人次是多少」→ 出现第 1/2 轮澄清，选项为「自然月 / 最近30天」，默认项有标记；选「自然月」→ 出现第 2/2 轮「仅门诊 / 门诊+急诊」；选完 → 出 SQL。
- [ ] 点「跳过，用默认值」→ 出 SQL，assumptions 里两项都标"按默认口径处理"。
- [ ] 输入「上个月外科住院患者的手术例数」→ 最多问 3 轮，不重复问同一个问题，最后出 SQL。
- [ ] SQL 卡片可复制、可下载 `.sql` 文件。
- [ ] 前端代码里 **没有** `openai`、没有 API Key 输入框、没有 `requests` 直连 DeepSeek。
- [ ] 后端未启动时，页面顶部提示"后端未连接"，不崩溃、不白屏。
- [ ] 同一个 query 提交第二次回答时，页面不会重复发请求（按钮已禁用/卡片已消失）。
- [ ] `pytest tests/frontend/test_api_client.py -q` 全绿（不联网）。
- [ ] 按 `docs/GETTING_STARTED.md` 的命令，两个终端能起前后端。

## 9. 启动方式（写进 GETTING_STARTED）

后端（终端 1，仓库根目录）：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

前端（终端 2，仓库根目录）：

```powershell
.venv\Scripts\python.exe -m pip install -r frontend\requirements.txt
.venv\Scripts\python.exe -m streamlit run frontend\app.py
```

浏览器打开 `http://localhost:8501`。前端只认 `MEDICAL_API_BASE_URL`（默认 `http://127.0.0.1:8000`），不再需要填 DeepSeek key——key 在后端 `.env`。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| Streamlit 每次交互重跑脚本，误发重复请求 | 所有请求由"按钮点击/表单提交"触发；提交后 `st.rerun()` 且卡片置空 |
| 刷新丢会话 | MVP 接受；P3 可选 `?session_id=` 深链或后端 `GET /sessions/{id}` |
| 后端首次请求要加载知识层 + DeepSeek，页面卡住 | `httpx` 超时设 120s，按钮期间显示 `st.spinner("正在生成 SQL…")` |
| 用户连点"跳过" | 提交后按钮区域立即被替换，不做重试队列 |
| 指标库与知识层不一致 | 指标只从 `GET /metrics` 取，前端不读 Excel |
| 前端误展示"查询结果" | 删除 `mock_execute`；结果区只放 SQL 与口径说明 |

## 11. 明确不做

- 不执行 SQL、不展示结果表格（等 C 的 SQLite 镜像和真实库接入）；
- 不做登录/鉴权、不做多用户隔离（`session_id` 仅内存）；
- 不做 React/Vue 版本；
- 不做流式输出、不做语音输入；
- 不改后端图结构（只加一个只读 `GET /metrics`）。
