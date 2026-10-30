# 启动文档

按顺序做三件事：**① 虚拟环境 + 依赖 → ② 配置 API Key → ③ 启动前后端**。

所有命令都在项目根目录执行：

```powershell
cd D:\02pythonLearn\SRP\medical-nl2sql-agent
```

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

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev]"                    # 后端：fastapi / langgraph / langchain-deepseek / sqlglot / pytest / ruff
pip install -r frontend\requirements.txt   # 前端：streamlit / httpx / python-dotenv
```

国内网络慢就加镜像：

```powershell
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r frontend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

只有要跑 Excel 转 JSON 的脚本时才需要额外装数据依赖：

```powershell
pip install -e ".[dev,data]"
```

验证：

```powershell
python -c "import fastapi, langgraph, langchain_deepseek, sqlglot, streamlit; print('deps ok')"
# deps ok
```

---

## 2. 配置 API Key

复制模板：

```powershell
copy .env.example .env
```

编辑 `.env`，至少填这三项：

```text
LLM_API_KEY=sk-你的DeepSeekKey
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com
```

注意：

- 文件名必须正好是 `.env`（不是 `..env`，也不是 `.env.txt`）；放在**项目根目录**。
- `.env` 已在 `.gitignore` 里，**不要提交**。
- Key 只给后端用。前端不填任何 key，它只调用后端。
- 后续 C 的模块会用到这些（现在用不到，可以先留默认）：`REFERENCE_DATE`、`SQLITE_DB_PATH`、`MAX_SQL_REPAIRS`、`MAX_REPAIR_ROUNDS`。

验证配置被正确读取：

```powershell
python -c "from app.config import settings; print(settings.llm_model, bool(settings.llm_api_key))"
# deepseek-chat True
```

没配 key 也能启动服务（`/health` 正常），只是提问时会返回 `status=error`。

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

- <http://127.0.0.1:8000/health> → `{"status":"ok","version":"0.1.0"}`
- <http://127.0.0.1:8000/docs> → 接口文档

```powershell
curl http://127.0.0.1:8000/health
```

> 知识层和 DeepSeek 客户端是**第一次请求时**才加载，所以首个问题会慢几秒，属正常。

### 3.2 终端 2：前端（Streamlit :8501）

```powershell
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
- 前端默认连 `http://127.0.0.1:8000`。要改后端地址：复制 `frontend\.env.example` 为 `frontend\.env`，修改 `MEDICAL_API_BASE_URL`，然后重启前端。

### 3.3 试两句

| 输入 | 期望 |
|---|---|
| `2025年1月急诊人次` | 直接出 SQL（页面标明"未执行"） |
| `上个月门诊人次是多少` | 第 1/2 轮「自然月 / 最近30天」→ 第 2/2 轮「仅门诊 / 门诊+急诊」→ 出 SQL |
| 任一澄清点「跳过，用默认值」 | 出 SQL，口径说明标"按默认口径处理" |

---

## 4. 跑测试（可选）

```powershell
.venv\Scripts\python.exe -m pytest tests -q          # 全量：后端 + 前端，不联网
.venv\Scripts\python.exe -m pytest tests\frontend -q # 只测前端 api_client（MockTransport）
.venv\Scripts\python.exe -m ruff check frontend tests app
```

LLM 用 `tests/fakes.py` 的 `FakeLLMClient` 替身，不需要 key、不需要启动服务。

---

## 5. 常见问题

### 浏览器打不开 `localhost:8501`（ERR_CONNECTION_REFUSED）

Streamlit 没真正启动。最常见是卡在 `Email:` 那一行等输入——回车即可。确认端口有没有在听：

```powershell
netstat -ano | findstr :8501
```

没有输出就说明没起来，回终端看报错。

### 页面顶部提示"后端未连接"

终端 1 没起，或端口/地址不一致。先 `curl http://127.0.0.1:8000/health`，再检查 `frontend\.env` 的 `MEDICAL_API_BASE_URL`。

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

### 安装慢 / SSL 错误

按 1.2 加清华镜像。

---

## 6. 其他：知识层自检

```powershell
python -m app.knowledge.validate --data-dir data
python -m app.knowledge.inspect "上个月门诊人次是多少"
```

期望：

```text
知识库校验通过
  schema: 13 tables, 791 columns
  metrics: 35
  gold: 35 (29 sql, 6 formula)
  glossary: 85 terms
  ambiguities: 8 rules
  examples: 15
```

前端与接口的详细说明见 `frontend/README.md`、`docs/API.md`；前端实现计划见 `docs/PLAN_FRONTEND.md`。

## 7. 下一步（C 就绪后）

1. 把 `app/graph/nodes/sql_generation.py` 内部换成 C 的 `generate_sql()`，图结构不变；
2. 加回静态校验 / 修复节点（`validate_sql`、`sql_repair`），放在 `generate_sql` 之后；
3. 用 C 的 SQLite 镜像跑 gold SQL 对比。
