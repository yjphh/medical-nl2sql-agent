# 前端开发文档（Streamlit）

医疗 NL2SQL 的对话界面：提问 → 逐轮澄清 → 生成 SQL（不执行）。三栏布局（Feishu 文档风格）：

```
┌──────────────┬────────────────┬──────────────────────────────────────┐
│ 侧边栏        │ 询问记录        │ 对话 + 澄清卡片 + SQL 卡片            │
│ 后端探活      │ （文档大纲）    │ （独立滚动，底部是输入框）            │
│ 会话 / 模式   │ 当前问题高亮    │                                      │
│ 指标库        │ 点击跳回原问题  │                                      │
│ trace 开关    │ 回答缩进在问题下│                                      │
└──────────────┴────────────────┴──────────────────────────────────────┘
```

前端**只调用 FastAPI**（`frontend/api_client.py` 是唯一 HTTP 出口），不直连任何大模型；
DeepSeek key 只在后端 `.env`。

## 1. 启动

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

后端地址与超时通过 `frontend/.env` 覆盖（默认 `http://127.0.0.1:8000` / 180 秒）：

```text
MEDICAL_API_BASE_URL=http://127.0.0.1:8000
MEDICAL_API_TIMEOUT=180
```

## 2. 目录

```text
frontend/
├── app.py                  # 页面编排：模式、三栏、提问/回答/恢复
├── api_client.py           # 唯一 HTTP 出口（不 import streamlit，可用 MockTransport 单测）
├── models.py               # 后端响应的 dataclass 视图
├── state.py                # session_state 默认值、模式快照/恢复、消息构造
├── sql_format.py           # 生成 SQL 的美化（sqlglot，失败回退正则）
├── ui/
│   ├── sidebar.py          # 后端探活、会话、模式、指标库、询问记录大纲
│   ├── chat.py             # 消息渲染、滚动、面板高度适配
│   ├── clarification.py    # 澄清卡片：选项 / 默认值 / 跳过 / 自由文本 / 轮次
│   └── result.py           # SQL 卡片、口径说明、澄清记录、调试 trace
├── requirements.txt        # streamlit / httpx / python-dotenv / sqlglot
└── .env.example
```

## 3. 状态模型 `state.py`

`st.session_state` 里与对话相关的键（`MODE_KEYS`）每个模式各一份：

| 键 | 含义 |
|---|---|
| `session_id` | 后端会话（每模式独立） |
| `messages` | 气泡列表，每条有 `mid`、`role`、`kind`（`question` / `answer` / `sql` / `error`） |
| `query_id` | 当前待澄清的查询 |
| `pending_clarification` | 待回答的澄清卡片 |
| `last_sql` | 最近一条 SQL |
| `recover_query_id` | 超时/断线后仍可取回的查询 |
| `current_question` | 当前问题（SQL 卡片要重复显示它） |
| `active_mid` | 询问记录里高亮的那条 |

模式切换 `switch_mode` 把上述键快照进 `mode_state[旧模式]`，再装载另一模式的快照，
所以「智能引导」的待答问题不会漏进「自由对话」。`reset_conversation` 只清当前模式；
注意 `reset_conversation` 把 `messages` 置空、其余键置 `None`。

其余键：`mode`、`mode_state`、`scroll_target`、`pending_ask`、`show_trace`、`_synced_mode`。

## 4. 一次提问的执行顺序

问题气泡必须**先出现**，再等后端。Streamlit 从上到下执行脚本，而输入框在脚本末尾，
所以不能在输入回调里直接调后端：

1. `st.chat_input` / 示例按钮 / 指标库按钮 → `queue_question(question)`：
   - 必要时 `POST /sessions` 建会话；
   - `add_message("user", question, kind="question")`，记 `current_question`、`active_mid`；
   - 设 `pending_ask = question`；
2. `st.rerun()`：本轮把消息流画出来（含新气泡），底部才执行到
   `_ask_backend(queued)`；
3. `_ask_backend` 生成客户端 `request_id`，调 `_with_progress("正在生成 SQL", ...)`：
   - 先画一行 `⏳ 正在生成 SQL… 已用 0 秒` 并滚到底部，因此答案再快也能看到状态；
   - 后台线程里发 `POST /queries`，主线程每 0.25s 刷新已用秒数；
4. 响应交给 `handle_response`：需要澄清就存 `pending_clarification` 并画卡片；
   成功就追加一条 `kind="sql"` 的消息（带 SQL、口径、澄清历史、trace），然后 `st.rerun()`。

澄清回答走 `clarification.render(on_answer)`：先 `add_message("user", 选项label, kind="answer")`
再 `POST /queries/{id}/answers`，失败时尝试回同步而不是丢掉已答轮次。

## 5. 布局与滚动

- **页面本身不滚**：最右边的滚轮只滚对话面板，标题不会被顶栏遮住。
- `.st-key-chat_scroll` 是对话面板容器，`CHAT_CSS` 给了一个视口高度兜底值；
  `chat.fit_chat_pane()` 每次运行注入一段 JS（`components.html(height=0)`，用时间戳强制重载），
  在主文档里测量"面板顶部到输入框之间"的剩余高度并设为面板高度，同时把
  `[data-testid="stMain"]` 的 `overflowY` 设为 `hidden`。
  窗口过矮（剩余 <160px）时放弃锁滚动，改为 `overflowY='auto'`，避免把输入框裁掉。
- 询问记录栏 `.st-key-hist_panel` 是 `position: sticky`，自己滚动，与对话栏互不影响。
- `chat.apply_scroll(mid)`：新消息增长时滚到底部；点击询问记录条目时
  `scrollIntoView({block:'start'})` 并加 `.msg-flash` 高亮 1.6 秒（JS 找不到元素时静默退出）。

## 6. 询问记录（大纲）

`sidebar.render_history()` 把当前模式的 `messages` 按问题分组：

- 每个 `kind="question"` 是一条，`kind="answer"` 缩进显示在所属问题下（`↳ 自然月`）；
- 条目是透明按钮（`jump_<mid>`），当前条目用 `jump_on_<mid>` 高亮成蓝色；
- 点击只设 `scroll_target` 与 `active_mid`，由按钮自身触发 rerun，不重跑查询。

## 7. SQL 卡片 `ui/result.py`

- 顶部重复显示原问题，滚动后也知道这条 SQL 回答的是哪句话；
- `sql_format.format_sql` 用 sqlglot（SQLite 方言）美化，解析失败时用关键词换行兜底；
- 空结果时标黄提示"镜像上命中 0 行"；否则提示"真实库未执行"；
- 可下载 `.sql`；
- 「口径说明」按来源显示：`source=default` ⚠️ 按默认口径处理，`code=sql` ℹ️ 模型补充说明，
  其他 ✅ 用户已确认；
- 折叠显示澄清记录与调试 trace（trace 仅在侧边栏开关打开时渲染）。

## 8. 失败恢复

- **连接失败**：提示检查后端；侧边栏「重新检测后端」清掉 health 缓存后 rerun，不需要刷新页面。
- **超时**：后端可能仍在执行，记下 `request_id` 并显示「取回上次结果」按钮，
  点击后 `GET /queries/{request_id}` 取回，不必重新提问。
- **回答澄清 409/404**：先 `GET /queries/{id}` 回同步（后台可能已推进），
  304/404 才提示重新提问；网络不可达时保留卡片，允许直接再点「确认」重试。
- **刷新页面**：`session_id` / `query_id` 写进 URL query string；只有**待回答的澄清**会在刷新后
  自动恢复，已完成/已失败的查询不重放。
- **取消本次提问**：只清 pending 与 query_id，聊天记录保留。

## 9. 模式

| 模式 | 后端 `mode` | 行为 |
|---|---|---|
| 智能引导 | `guided` | 先检测歧义、逐轮澄清，再生成 SQL |
| 自由对话 | `direct` | 一次模型调用直接出 SQL，用于对照实验 |

模式在侧边栏切换，待澄清期间禁用（避免线程回调把结果写进另一个模式）。两种模式的对话、
`session_id`、询问记录各自独立保存。

## 10. 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\frontend -q
```

- `test_api_client.py`：`httpx.MockTransport` 验证 URL/payload、错误分类（timeout/connect/http）、
  `request_id` 与 `mode` 透传；
- `test_app_flow.py`：Streamlit `AppTest` 在进程内打桩 `BackendClient`，覆盖模式隔离、
  澄清卡片保留、409 回同步、刷新恢复、问题气泡先于请求出现、SQL 卡片显示原问题、多行 SQL；
- `test_sql_format.py`：美化输出含换行、`FROM` 独立成行、非法 SQL 走兜底不抛错。

两者都不联网、不需要启动后端。

## 11. 手工验收清单

- [ ] 「2025年1月急诊人次」→ 直接出 SQL，页面写明"未执行"。
- [ ] 「上个月门诊人次是多少」→ 第 1/2 轮「自然月 / 最近30天」→ 第 2/2 轮「仅门诊 / 门诊+急诊」→ 出 SQL。
- [ ] 提交后**立刻**看到自己的问题气泡，其下方显示「⏳ 正在生成 SQL… 已用 N 秒」。
- [ ] 点「跳过，用默认值」→ 出 SQL，口径说明里标"按默认口径处理"。
- [ ] 「上个月外科住院患者的手术例数」→ 最多 3 轮，问题不重复。
- [ ] SQL 可复制、可下载 `.sql`，且为多行带行号。
- [ ] 点侧栏「询问记录」里的条目 → 对话滚回该问题并高亮；最右侧滚轮只滚动对话栏。
- [ ] 切到「自由对话」问同一句 → 无澄清直接出 SQL；切回「智能引导」→ 原对话恢复。
- [ ] 后端停掉后刷新页面 → 顶部提示"后端未连接"，不白屏；重启后端后点「重新检测后端」恢复。
- [ ] 澄清卡片出现时停掉后端再点「确认」→ 提示可再点「确认」重试，卡片仍在。
- [ ] 澄清进行中刷新页面 → 待回答的问题被恢复（URL 里的 `?query=`）。
- [ ] 页面上没有 API Key 输入框，前端代码里没有 `openai`。

## 12. 说明

- 请求期间显示已用秒数。Streamlit 单线程执行脚本，按钮无法在请求进行中触发，
  所以没有「取消进行中的请求」；中断后可用「取回上次结果」拿回后端已生成的 SQL。
- 本轮不执行 SQL、不展示结果表。
- `schema_links` 只在接口响应与 trace 里，页面不单独展示。
- 直连模式不写 checkpointer，取回时只有状态与 SQL，没有口径与 trace（见 [API.md](API.md)）。
