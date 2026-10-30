# 启动文档

按顺序做四件事：**① 虚拟环境 + 依赖 → ② 生成 SQLite 镜像 → ③ 配置 API Key → ④ 启动前后端**。

所有命令都在项目根目录执行：

```powershell
cd D:\02pythonLearn\SRP\medical-nl2sql-agent
```

---

## 0. 前置条件

- Windows + PowerShell（cmd / Git Bash 也可，差异在 1.1 说明）
- Python **3.12**（不要用 3.14）
- 能访问 PyPI
- 一个 DeepSeek API Key

```powershell
py -0p
# 应能看到 -V:3.12
```

---

## 1. 虚拟环境 + 依赖

### 1.1 创建并激活虚拟环境

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

PowerShell 提示"禁止运行脚本"时先执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

其他终端：

```bat
:: cmd
.venv\Scripts\activate.bat
```

```bash
# Git Bash
source .venv/Scripts/activate
```

确认解释器指向项目内的 `.venv`：

```powershell
python --version   # Python 3.12.x
where python       # 第一行应是 ...\medical-nl2sql-agent\.venv\Scripts\python.exe
```

> 不想激活也可以：后文所有 `python` 换成 `.venv\Scripts\python.exe`，效果相同。

### 1.2 安装依赖

后端依赖在 `pyproject.toml`，前端在 `frontend/requirements.txt`，**仓库根目录的 `requirements.txt` 已删除**：

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev]"                    # fastapi / langgraph / langchain-deepseek / sqlglot / openai / pytest / ruff
pip install -r frontend\requirements.txt   # streamlit / httpx / python-dotenv / sqlglot
```

国内网络慢就加镜像：

```powershell
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r frontend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证：

```powershell
python -c "import fastapi, langgraph, langchain_deepseek, sqlglot, streamlit; print('deps ok')"
# deps ok
```

### 1.3 生成 SQLite 镜像（必做）

SQL 生成器会先在本地 SQLite 镜像上执行、自修正、投票，所以镜像库是**启动前置条件**，不是可选项。
它由 `build_db` 从 `data/schema.json` 生成，**不需要单独安装 SQLite**（用的是 Python 标准库），
也不需要手工导入数据：

```powershell
.venv\Scripts\python.exe -m app.tools.sql_agent.build_db
```

期望最后几行类似：

```text
visit: 240 rows
...
examples: 15/15 executed with rows
```

- 幂等：重跑会重建库。
- 产物 `data/synthetic/medical.db` 被 `.gitignore` 忽略，**换机器 / 新 clone 必须重跑一次**；
  否则每条查询都会因 `database_not_found` 失败。
- 镜像只有合成数据（固定 `seed=42`，PII 为假值），不连接真实生产库。
- 数据窗口按 `REFERENCE_DATE` 反推 12 个月（默认 `2025-12-31` → 2025-01-02 ~ 2025-12-27），
  与 `data/sql_examples_sqlite.json` 里固定的 2025 年窗口一致。

---

## 2. 配置 API Key

复制模板：

```powershell
copy .env.example .env
```

编辑 `.env`，至少填这三项：

```text
LLM_API_KEY=sk-你的DeepSeekKey
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com
```

注意：

- 文件名必须正好是 `.env`（不是 `..env`，也不是 `.env.txt`）；放在**项目根目录**。
- `.env` 已在 `.gitignore` 里，**不要提交**。
- Key 只给后端用。前端不填任何 key，它只调用后端。
- 其余配置（澄清轮数、SQLite 镜像路径、超时、候选数）都有默认值，见 `.env.example`。

验证配置被正确读取：

```powershell
python -c "from app.config import settings; print(settings.llm_model, bool(settings.llm_api_key))"
# deepseek-chat True
```

没配 key 也能启动服务（`/health` 正常），只是提问时会返回 `status=error`（`llm_unavailable`）。

---

## 3. 启动

开**两个**终端，都在项目根目录。先后端，再前端。

### 3.1 终端 1：后端（FastAPI :8000）

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

期望输出：

```text
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

验证（浏览器或命令行）：

- <http://127.0.0.1:8000/health> → `{"status":"ok",...}`；它会真的探测镜像库与知识文件，
  缺库时返回 `status: "degraded"` 并在 `message` 里直接给出修复命令
- <http://127.0.0.1:8000/docs> → 交互式接口文档

```powershell
curl http://127.0.0.1:8000/health
```

> 知识层与 DeepSeek 客户端是**第一次请求时**才加载，所以首个问题会慢几秒，属正常。

**只支持单 worker**：会话、查询索引和 graph checkpoint 都存在本地 SQLite
（`data/sessions.db`、`data/checkpoints.db`），单进程可跨重启恢复，但多个 worker 并发写同一文件没有收益。
需要水平扩展时再换共享存储（Postgres checkpointer + 正式数据库）。过期记录（`SESSION_TTL_HOURS`，默认 24h）
在启动时清理，对应的 checkpoint 线程一并删除。

### 3.2 终端 2：前端（Streamlit :8501）

```powershell
.venv\Scripts\python.exe -m pip install -r frontend\requirements.txt   # 首次
.venv\Scripts\python.exe -m streamlit run frontend\app.py
```

期望输出：

```text
  You can now view your Streamlit app in your browser.
  Local URL: http://localhost:8501
```

浏览器打开 <http://localhost:8501>，侧边栏应显示"后端已连接"。

- 首次启动可能先问一行 `Email:` —— **直接回车留空**即可
- 只有看到 `Local URL` 那行，端口才真正在监听；在此之前浏览器打开会报"拒绝连接"。
- 前端默认连 `http://127.0.0.1:8000`。要改后端地址：复制 `frontend\.env.example` 为
  `frontend\.env`，修改 `MEDICAL_API_BASE_URL`（可加 `MEDICAL_API_TIMEOUT`），然后重启前端。

### 3.3 试两句

| 输入 | 期望 |
|---|---|
| `2025年1月急诊人次` | 直接出 SQL（页面标明"未执行"） |
| `上个月门诊人次是多少` | 第 1/2 轮「自然月 / 最近30天」→ 第 2/2 轮「仅门诊 / 门诊+急诊」→ 出 SQL |
| 任一澄清点「跳过，用默认值」 | 出 SQL，口径说明标"按默认口径处理" |

前端还有「自由对话」模式（一次模型调用直接出 SQL，无澄清），用于对照实验，见
[FRONTEND.md](FRONTEND.md)。

---

## 4. 开发工作流

### 4.1 常用命令

```powershell
.venv\Scripts\python.exe -m pytest tests -q            # 全量：后端 + 前端，不联网
.venv\Scripts\python.exe -m ruff check app frontend tests
.venv\Scripts\python.exe -m app.tools.sql_agent.build_db   # 重建镜像（改了 schema/示例后）
.venv\Scripts\python.exe scripts\check_questions.py --check # 对运行中的后端跑 10 条冒烟（需要 key）
```

### 4.2 测试结构

```text
tests/
├── fakes.py              # FakeLLMClient / FakeSQLAgentLLM / FakeExecutor：不联网、不读 .env
├── unit/
│   ├── graph/            # 图分支、澄清子图、父子图字段传递
│   ├── knowledge/        # 指标检索、schema 上下文、上下文预算
│   ├── memory/           # checkpointer、会话仓库与 TTL
│   └── tools/            # build_db / safety / selector / core / 子图 / import 边界
├── integration/          # FastAPI + 图（fake 依赖，不联网）
└── frontend/             # api_client（MockTransport）+ AppTest 主流程 + SQL 格式化
```

LLM 用 `tests/fakes.py` 的替身，不需要 key、不需要启动服务。CI
（`.github/workflows/ci.yml`）在 Python 3.12 上跑同样的 `ruff` + `pytest`，`LLM_API_KEY` 置空。

### 4.3 代码风格

- Ruff 配置见 `pyproject.toml`：行宽 100、目标 Python 3.10+。
- 类型注解使用 3.10+ 语法（`str | None`）；模块尽量保持无副作用，依赖通过参数注入。
- 注释解释"为什么"，不重复代码做了什么。

### 4.4 改动后的最低验证

| 改动 | 至少跑 |
|---|---|
| 任意 Python 代码 | `ruff check app frontend tests` + `pytest tests -q` |
| `data/*.json` 或 `build_db.py` | 重建镜像 + 知识加载测试 + `scripts/check_questions.py`（有 key 时） |
| 图节点 / 澄清逻辑 | `tests/unit/graph` + `tests/integration` |
| 前端 | `tests/frontend` + 手工走查 [FRONTEND.md](FRONTEND.md) 的验收清单 |

---

## 5. 常见问题

### 浏览器打不开 `localhost:8501`（ERR_CONNECTION_REFUSED）

Streamlit 没真正启动。最常见是卡在 `Email:` 那一行等输入——回车即可。确认端口有没有在听：

```powershell
netstat -ano | findstr :8501
```

没有输出就说明没起来，回终端看报错。

### 页面顶部提示"后端未连接"

终端 1 没起，或端口/地址不一致。先 `curl http://127.0.0.1:8000/health`，再检查
`frontend\.env` 的 `MEDICAL_API_BASE_URL`。

### `error while attempting to bind on address ... 10048`（端口被占用）

换端口，前后端要一起改：

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8001
```

并在 `frontend\.env` 写 `MEDICAL_API_BASE_URL=http://127.0.0.1:8001`（同时确认没有多开 uvicorn）。

### `ModuleNotFoundError: No module named 'langgraph'`

用了系统 Python。`where python` 应指向 `.venv`；或统一改用 `.venv\Scripts\python.exe`。

### PowerShell 无法激活 / 默认 Python 是 3.14

激活问题按 1.1 处理；3.14 请重新 `py -3.12 -m venv .venv` 建环境。

### 提问返回 `status: error`、提示鉴权失败

`.env` 文件名不对（比如 `..env`）或 key 无效。用第 2 节的验证命令确认。

### `/health` 返回 `degraded`

`message` 会直接给出修复命令：镜像缺失时是 `build_db` 命令；知识文件异常时是
`KnowledgeDataError` 的原文（缺文件、版本不一致、引用不存在的列/指标等）。
知识文件的结构与扩展方式见 [MEMBER_A_KNOWLEDGE.md](MEMBER_A_KNOWLEDGE.md)。

### 安装慢 / SSL 错误

按 1.2 加清华镜像。

---

## 6. 后续方向

1. **金标准评测**：`data/gold_sql.json`（29 条 SQL + 6 条公式）目前只做引用校验，没有任何代码执行对比；
   下一步是转出 SQLite 版 gold，在镜像上跑 gold 与 Agent 生成 SQL 的结果集比对，给出 per-metric 通过率。
2. **多 worker / 共享存储**：checkpoint 换成 Postgres，会话仓库换成正式数据库，再放开多 worker。
3. **连接真实库**：本轮只在 SQLite 镜像上执行；接生产库前先确认方言（Excel 的 gold SQL 是 Oracle 家族写法）
   并只给只读账号。
