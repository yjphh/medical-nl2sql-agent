# 组员 C 开发计划：ReFoRCE-lite（多候选 + SQLite 自修正）

> 目标：B 给出澄清后的完整问题，C 用**纯 Python**实现一个简化版 ReFoRCE agent：
> 多候选生成 → SQLite 执行 → 错误回传修正 → 结果集投票 → 返回最终 SQL。
>
> 不使用 LangChain / LangGraph；不接官方 ReFoRCE；暂不做评测。

## 1. 分工边界

- **A**：提供 `data/schema.json`（数据库模式 + 业务口径 + 字段说明）和 `data/sql_examples_sqlite.json`（已改写成 SQLite 方言的示例 SQL）。
- **B**：多轮追问澄清后，输出一条**完整、无歧义的问题**；问题涉及的字段都在 A 的 schema 里。
- **C**：接收问题，读 A 的 schema，生成 SQLite SQL；在本地 SQLite 镜像上执行、自修正、多候选投票；返回最终 SQL。
- C 不负责追问、不做评测、不连接真实生产库。

## 2. 目录结构

```text
app/tools/sql_agent/
├── __init__.py
├── build_db.py       # schema.json -> SQLite 镜像 + 样例数据
├── llm.py            # OpenAI-compatible chat，支持 n 条候选
├── prompt.py         # schema 文本、生成/修复 prompt
├── safety.py         # 只读、单语句、PII 的简单检查
├── executor.py       # SQLite 只读执行、超时、行数限制
├── selector.py       # 结果哈希、候选投票、静态打分
├── agent.py          # 自适应多候选主循环
└── cli.py            # 命令行演示

data/
├── schema.json                   # A 提供
├── sql_examples_sqlite.json      # A 提供
└── synthetic/
    └── medical.db                # C 生成，用于执行验证

docs/
├── PLAN_MEMBER_C.md              # 本文件
└── SQLITE_AGENT.md               # 使用/配置/限制说明
```

## 3. 主流程（自适应多候选）

```text
B 的完整问题
  ↓
读取 A 的 schema + SQLite 示例
  ↓
估计问题复杂度 → 决定候选数 N（1/2/3）
  ↓
LLM 生成 N 条候选 SQL
  ↓
去重 → 简单安全检查 → 在 SQLite 镜像执行
  ↓
执行失败的候选：把错误喂回 LLM，修正 1 次（共享预算）
  ↓
按结果集哈希投票：
  ├─ 两个候选结果一致 → 直接采用，提前结束
  ├─ 两个候选结果不一致 → 追加生成第 3 条（escalation）
  └─ 全部失败/为空 → 静态分兜底，或返回错误
  ↓
返回最终 SQL + 候选 + 投票 + 成本统计
```

## 4. 成本控制与创意（重点）

这是 ReFoRCE 的简化版，但保留“多候选 + 执行反馈 + 投票”，同时控制成本：

1. **自适应候选数**
   - 简单问题（短、无多指标/分组/排名）：1 条。
   - 普通问题：2 条。
   - 复杂问题（“排名/占比/各/每/平均/同比/环比”等）：最多 3 条，但**先出 2 条**，结果不一致才追加第 3 条。
   - 用关键词启发式，不额外调 LLM。

2. **共识提前停止**
   - 前两条候选执行结果一致（结果哈希相同）→ 直接采用，不再生成/执行第 3 条。
   - 大部分问题只花 2 次生成 + 2 次执行。

3. **不一致才升级（escalation）**
   - 前两条结果不一致 → 才生成第 3 条。
   - 避免“所有问题都跑满 3 条”的浪费。

4. **只修失败候选**
   - 执行成功的候选不修。
   - 只有报错的候选进入修复；每条最多修 1 次，总修复预算 2 次。
   - 空结果不默认视为错误，避免把合理空结果改坏。

5. **去重再执行**
   - 两条候选 SQL 归一化后相同 → 只执行一次。

6. **静态预过滤**
   - 非 SELECT、写操作、多语句、PII 直接拒绝，不浪费执行。

7. **结果哈希采样**
   - 只取前 50 行 + 是否截断 + 行数做哈希，不把全量结果放内存。

8. **不用 LLM judge**
   - 选择完全用结果哈希 + 静态分，避免额外 LLM 调用。

9. **可选模型分层**
   - 生成用便宜/快模型，修复用更强模型；默认同一个模型，可通过配置切换。

10. **缓存 schema/示例**
    - schema 文本和 few-shot 在进程内缓存，不重复读文件。

返回结果里带 `llm_calls / executions / repairs / elapsed_ms`，方便看成本。

## 5. 示例代码

以下代码可直接作为实现起点。

### 5.1 `build_db.py`：建 SQLite 镜像

```python
import json
import random
import sqlite3
from pathlib import Path


def sqlite_type(t: str) -> str:
    t = t.upper()
    if "INT" in t:
        return "INTEGER"
    if any(x in t for x in ("NUM", "DEC", "NUMBER", "FLOAT", "DOUBLE", "REAL")):
        return "REAL"
    return "TEXT"


def fake_value(col: dict, i: int, rng: random.Random):
    """按列名/类型/枚举造简单假值；_id 用固定范围，保证 join 能对上。"""
    if col.get("enum_values"):
        return rng.choice(col["enum_values"])["value"]
    name = col["name"].lower()
    typ = col["type"].upper()
    if name.endswith("_id"):
        return str(i % 20 + 1)
    if "DATE" in typ or "TIME" in typ:
        return f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    if "INT" in typ:
        return rng.randint(1, 100)
    if any(x in typ for x in ("NUM", "DEC", "NUMBER", "FLOAT", "DOUBLE", "REAL")):
        return round(rng.uniform(0, 10000), 2)
    if col.get("is_pii"):
        return f"P{i:04d}"
    return f"{col['name']}_{i}"


def build(
    schema_path: str = "data/schema.json",
    db_path: str = "data/synthetic/medical.db",
    rows: int = 20,
    seed: int = 42,
) -> None:
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    rng = random.Random(seed)

    for table in schema["tables"]:
        name = table["name"]
        cols = table["columns"]
        defs = ", ".join(f'"{c["name"]}" {sqlite_type(c["type"])}' for c in cols)
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')
        conn.execute(f'CREATE TABLE "{name}" ({defs})')
        placeholders = ", ".join("?" for _ in cols)
        for i in range(rows):
            values = [fake_value(c, i, rng) for c in cols]
            conn.execute(f'INSERT INTO "{name}" VALUES ({placeholders})', values)

    conn.commit()
    conn.close()
    print(f"SQLite DB created: {db_path}")


if __name__ == "__main__":
    build()
```

### 5.2 `prompt.py`：schema、规则、生成/修复 prompt

```python
import json
from functools import lru_cache
from pathlib import Path

SQLITE_RULES = """你是医疗数据 SQLite SQL 生成器。
只输出一条只读 SELECT / WITH ... SELECT。
规则：
1. 只使用给定的表和列，不要编造。
2. 方言是 SQLite：IFNULL/COALESCE、strftime、||、LIMIT、双引号。
3. 禁止 NVL、TO_CHAR、ROWNUM、DATE_FORMAT、反引号。
4. 日期以 TEXT 'YYYY-MM-DD' 存储，过滤用 >= / <。
5. 多表必须写 JOIN 条件，避免笛卡尔积。
6. 不输出 id_card、姓名等 PII 列；COUNT(DISTINCT ...) 去重除外。
7. 只输出 SQL，不要解释。
"""


@lru_cache(maxsize=1)
def load_schema_text(path: str = "data/schema.json") -> str:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    lines: list[str] = []
    for table in data["tables"]:
        lines.append(f"## {table['name']} {table.get('description', '')}")
        for col in table["columns"]:
            extra = ""
            if col.get("enum_values"):
                values = ", ".join(
                    f"{e['value']}={e['label']}" for e in col["enum_values"]
                )
                extra += f" 枚举: {values}"
            if col.get("is_pii"):
                extra += " [PII 禁止输出]"
            lines.append(
                f"- {col['name']} {col['type']} "
                f"{col.get('description', '')}{extra}"
            )
        for rel in table.get("relationships", []):
            lines.append(f"- 关系: {rel['on']}")
        lines.append("")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def load_examples_text(path: str = "data/sql_examples_sqlite.json") -> str:
    p = Path(path)
    if not p.exists():
        return ""
    data = json.loads(p.read_text(encoding="utf-8"))
    return "\n\n".join(
        f"问题：{e['question']}\nSQL：{e['sql']}" for e in data.get("examples", [])
    )


def build_messages(
    question: str,
    last_sql: str | None = None,
    last_error: str | None = None,
) -> list[dict]:
    user = f"问题：{question}\n\nSchema：\n{load_schema_text()}"
    examples = load_examples_text()
    if examples:
        user += f"\n\n示例：\n{examples}"
    if last_sql and last_error:
        user += (
            f"\n\n上一条 SQL 执行失败：\n{last_sql}\n"
            f"错误：\n{last_error}\n请修正，只输出 SQL。"
        )
    return [
        {"role": "system", "content": SQLITE_RULES},
        {"role": "user", "content": user},
    ]
```

### 5.3 `llm.py`：支持 n 条候选

```python
from openai import OpenAI

from app.config import settings

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
        )
    return _client


def chat(
    messages: list[dict],
    temperature: float = 0.2,
    n: int = 1,
) -> list[str]:
    """返回 n 条文本；provider 不支持 n 时循环调用。"""
    if n <= 1:
        resp = _get_client().chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
        )
        return [resp.choices[0].message.content or ""]

    try:
        resp = _get_client().chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            n=n,
        )
        return [c.message.content or "" for c in resp.choices]
    except Exception:
        return [
            chat(messages, temperature=temperature, n=1)[0] for _ in range(n)
        ]
```

### 5.4 `safety.py`：只读、单语句、PII 简单检查

```python
import re

WRITE_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
    re.IGNORECASE,
)
PII_RE = re.compile(r"\b(id_card|patient_name|name)\b", re.IGNORECASE)


def check_sql(sql: str, pii_columns: list[str] | None = None) -> tuple[bool, str, bool]:
    """返回 (safe, reason, has_pii)。"""
    s = sql.strip()
    if not re.match(r"(?is)^(select|with)\b", s):
        return False, "只允许 SELECT / WITH", False
    if ";" in s:
        return False, "不允许多条语句", False
    if WRITE_RE.search(s):
        return False, "检测到写操作", False

    pii_hit = False
    cols = pii_columns or ["id_card", "patient_name", "name"]
    for col in cols:
        if re.search(rf"(?i)\b{re.escape(col)}\b", s):
            # COUNT(DISTINCT id_card ...) 允许作为去重键
            if not re.search(
                rf"(?is)count\s*\(\s*distinct[^)]*\b{re.escape(col)}\b", s
            ):
                pii_hit = True
    return True, "", pii_hit
```

> MVP 用正则，能挡住明显问题；后续可换 `sqlglot`。

### 5.5 `executor.py`：SQLite 只读执行

```python
import sqlite3
import time


def execute_sql(
    sql: str,
    db_path: str,
    timeout: int = 5,
    max_rows: int = 50,
) -> dict:
    """返回 {ok, columns, rows, truncated, error, elapsed_ms}。"""
    start = time.monotonic()
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON")
        deadline = start + timeout
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0,
            10000,
        )
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {
            "ok": True,
            "columns": columns,
            "rows": rows,
            "truncated": truncated,
            "error": None,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "columns": [],
            "rows": [],
            "truncated": False,
            "error": str(exc),
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }
    finally:
        if conn is not None:
            conn.close()
```

### 5.6 `selector.py`：结果哈希 + 投票

```python
import hashlib
import json


def result_hash(rows: list[tuple], truncated: bool = False) -> str:
    """只对前 50 行 + 是否截断 + 行数做哈希；忽略列别名和行顺序。"""
    normalized = sorted(
        tuple("" if v is None else str(v) for v in row) for row in rows
    )
    payload = json.dumps(
        {"n": len(normalized), "truncated": truncated, "rows": normalized},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def static_score(c: dict) -> int:
    score = 0
    if c.get("safe"):
        score += 100
    if c.get("exec_ok"):
        score += 20
    if c.get("rows"):
        score += 10
    if not c.get("has_pii"):
        score += 10
    score -= c.get("join_count", 0) * 2
    score -= len(c.get("sql", "")) // 200
    return score


def vote(candidates: list[dict]) -> tuple[dict | None, int, str]:
    """
    候选字段：sql, safe, exec_ok, rows, truncated, has_pii, join_count。
    返回 (best, votes, reason)。
    """
    executed = [c for c in candidates if c.get("safe") and c.get("exec_ok")]
    if not executed:
        valid = [c for c in candidates if c.get("safe")]
        if not valid:
            return None, 0, "all_invalid"
        best = max(valid, key=static_score)
        return best, 0, "no_execution_verified"

    groups: dict[str, list[dict]] = {}
    for c in executed:
        if not c.get("rows"):
            h = "empty"
        else:
            h = result_hash(c["rows"], c.get("truncated", False))
        groups.setdefault(h, []).append(c)

    non_empty = {h: g for h, g in groups.items() if h != "empty"}
    pool = non_empty or groups
    best_hash = max(
        pool,
        key=lambda h: (len(pool[h]), max(static_score(c) for c in pool[h])),
    )
    group = pool[best_hash]
    best = max(group, key=static_score)
    reason = f"{len(group)}/{len(executed)} candidates produced the same result"
    if best_hash == "empty":
        reason = "all candidates returned empty; selected by static score"
    return best, len(group), reason
```

### 5.7 `agent.py`：自适应多候选 + 修正

```python
import re
import time
from pathlib import Path

from app.config import settings
from app.tools.sql_agent.executor import execute_sql
from app.tools.sql_agent.llm import chat
from app.tools.sql_agent.prompt import build_messages
from app.tools.sql_agent.safety import check_sql
from app.tools.sql_agent.selector import vote

COMPLEX_WORDS = ["排名", "占比", "比例", "同比", "环比", "平均", "各", "每", "趋势", "top"]
JOIN_WORDS = ["和", "与", "对比", "分别"]
PII_COLS = ["id_card", "patient_name", "name"]


def extract_sql(text: str) -> str:
    match = re.search(r"\x60{3}(?:sql)?\s*(.*?)\x60{3}", text, re.S | re.IGNORECASE)
    sql = match.group(1) if match else text
    return sql.strip().rstrip(";")


def estimate_complexity(question: str) -> str:
    if any(w in question for w in COMPLEX_WORDS):
        return "complex"
    if any(w in question for w in JOIN_WORDS):
        return "normal"
    if len(question) <= 12:
        return "simple"
    return "normal"


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().lower().rstrip(";")


def _evaluate(sql: str, db_path: str, stats: dict) -> dict:
    safe, reason, has_pii = check_sql(sql, PII_COLS)
    item = {
        "sql": sql,
        "safe": safe,
        "reason": reason,
        "has_pii": has_pii,
        "exec_ok": False,
        "rows": [],
        "truncated": False,
        "join_count": len(re.findall(r"(?i)\bjoin\b", sql)),
    }
    if not safe:
        return item

    stats["executions"] += 1
    result = execute_sql(sql, db_path)
    item["exec_ok"] = result["ok"]
    item["rows"] = result["rows"]
    item["truncated"] = result["truncated"]
    item["error"] = result["error"]
    return item


def generate_sql(
    question: str,
    db_path: str | None = None,
    max_candidates: int | None = None,
    max_repairs: int | None = None,
) -> dict:
    start = time.monotonic()
    db_path = db_path or settings.sqlite_db_path
    max_repairs = max_repairs if max_repairs is not None else settings.max_repair_rounds
    stats = {"llm_calls": 0, "executions": 0, "repairs": 0}

    if not Path(db_path).exists():
        return {
            "sql": None,
            "verified": False,
            "candidates": [],
            "selected_reason": "mirror_missing",
            "votes": 0,
            "llm_calls": 0,
            "executions": 0,
            "repairs": 0,
            "elapsed_ms": 0,
            "error": "mirror_missing",
        }

    complexity = estimate_complexity(question)
    max_n = max_candidates or {"simple": 1, "normal": 2, "complex": 3}[complexity]
    initial_n = 1 if max_n <= 1 else 2

    # 1. 先生成 1 或 2 条；复杂问题不一致时才追加第 3 条
    messages = build_messages(question)
    stats["llm_calls"] += 1
    raw_list = chat(messages, temperature=0.7, n=initial_n)

    candidates: list[dict] = []
    seen: set[str] = set()
    for raw in raw_list:
        sql = extract_sql(raw)
        key = normalize_sql(sql)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(_evaluate(sql, db_path, stats))

    # 2. 失败候选修正（共享预算）
    repaired_budget = max_repairs
    for item in list(candidates):
        if item["exec_ok"] or repaired_budget <= 0:
            continue
        stats["repairs"] += 1
        repaired_budget -= 1
        stats["llm_calls"] += 1
        messages = build_messages(
            question,
            last_sql=item["sql"],
            last_error=item.get("error") or item.get("reason") or "unknown",
        )
        raw = chat(messages, temperature=0.0, n=1)[0]
        fixed_sql = extract_sql(raw)
        fixed = _evaluate(fixed_sql, db_path, stats)
        fixed["repaired_from"] = item["sql"]
        candidates.append(fixed)

    # 3. 两候选不一致 → 升级第 3 条
    executed = [c for c in candidates if c["exec_ok"] and c["safe"]]
    if len(executed) >= 2:
        from app.tools.sql_agent.selector import result_hash

        hashes = {
            result_hash(c["rows"], c.get("truncated", False))
            if c["rows"] else "empty"
            for c in executed
        }
        need_extra = max_n >= 3 and (len(hashes) > 1 or len(executed) == 1)
        if need_extra and len(candidates) < 3:
            stats["llm_calls"] += 1
            extra = chat(build_messages(question), temperature=0.7, n=1)[0]
            sql = extract_sql(extra)
            if normalize_sql(sql) not in seen:
                candidates.append(_evaluate(sql, db_path, stats))

    # 4. 投票
    best, votes, reason = vote(candidates)
    if best is None:
        return {
            "sql": None,
            "verified": False,
            "candidates": [c["sql"] for c in candidates],
            "selected_reason": reason,
            "votes": 0,
            "llm_calls": stats["llm_calls"],
            "executions": stats["executions"],
            "repairs": stats["repairs"],
            "elapsed_ms": int((time.monotonic() - start) * 1000),
            "error": "all_candidates_invalid",
        }

    return {
        "sql": best["sql"],
        "verified": best["exec_ok"],
        "result_empty": not best["rows"],
        "candidates": [c["sql"] for c in candidates],
        "selected_reason": reason,
        "votes": votes,
        "llm_calls": stats["llm_calls"],
        "executions": stats["executions"],
        "repairs": stats["repairs"],
        "elapsed_ms": int((time.monotonic() - start) * 1000),
        "error": None,
    }
```

### 5.8 `cli.py`：命令行演示

```python
import json
import sys

from app.tools.sql_agent.agent import generate_sql


if __name__ == "__main__":
    question = " ".join(sys.argv[1:])
    result = generate_sql(question)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
```

运行：

```bash
python -m app.tools.sql_agent.build_db
python -m app.tools.sql_agent.cli "2025年1月门诊人次是多少"
```

## 6. 配置

在 `app/config.py` 增加：

```python
sqlite_db_path: str = "data/synthetic/medical.db"
max_repair_rounds: int = 2
```

`.env.example` 增加：

```text
SQLITE_DB_PATH=data/synthetic/medical.db
MAX_REPAIR_ROUNDS=2
```

LLM 复用现有的 `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL`。  
DeepSeek 默认：`LLM_MODEL=deepseek-chat`，`LLM_BASE_URL=https://api.deepseek.com`。  
C 用 `openai` SDK 作为 OpenAI-compatible 客户端；`langchain-deepseek` 只给 B 用。

## 7. 开发顺序（4 天）

| 天 | 任务 | 交付 |
|---|---|---|
| D1 | `build_db.py` + `prompt.py` 的 schema/示例加载；生成 `medical.db` | 能建库、能打印 schema 文本 |
| D2 | `llm.py` + 单候选生成 + `safety.py` + `executor.py` | 一条 SQL 能执行成功 |
| D3 | `selector.py` + `agent.py` 的多候选、投票、修正、升级 | 多候选闭环 |
| D4 | `cli.py` + 与 B 联调 + `docs/SQLITE_AGENT.md` | 可演示、可交接 |

## 8. 验收标准

- [ ] `python -m app.tools.sql_agent.build_db` 能从 `data/schema.json` 建出 `data/synthetic/medical.db`，所有表都有 20 行样例数据。
- [ ] `python -m app.tools.sql_agent.cli "2025年1月门诊人次是多少"` 返回非空 SQL，`verified=true`，`result` 非空。
- [ ] 普通/复杂问题通常返回 2 条候选；如果两条生成完全一致会被去重，允许只剩 1 条。简单问题允许 1 条。
- [ ] 前两条候选结果一致时，`llm_calls`/`executions` 不继续增长（共识提前停止）。
- [ ] 复杂问题前两条候选结果不一致时，会追加第 3 条（escalation），`candidates` 数量增加；普通问题不会无条件跑 3 条。
- [ ] 执行失败的候选会触发修复；修复预算最多 2 次；仍失败返回 `verified=false` 和错误，不崩溃。
- [ ] `safety.py` 拒绝 `DELETE/UPDATE/DROP`、多语句、非 SELECT；PII 列被标记。
- [ ] 执行器只读、5 秒超时、最多取 50 行；不会连接真实生产库。
- [ ] 返回结果包含 `llm_calls / executions / repairs / elapsed_ms`，能看出成本。
- [ ] B 能直接调用：

```python
from app.tools.sql_agent.agent import generate_sql
result = generate_sql("2025年1月门诊人次是多少")
print(result["sql"], result["verified"])
```

- [ ] 不 import `langchain` / `langgraph` / `app.graph` / `app.api`。
- [ ] `docs/SQLITE_AGENT.md` 写清建库、配置、CLI、SQLite 方言限制。

## 9. 与 B 的接口

```python
def generate_sql(
    question: str,
    db_path: str | None = None,
    max_candidates: int | None = None,
    max_repairs: int = 2,
) -> dict:
    ...
```

B 只需要传问题；schema 由 C 读取 A 的 `data/schema.json`。返回：

```json
{
  "sql": "SELECT ...",
  "verified": true,
  "result_empty": false,
  "candidates": ["SELECT ...", "SELECT ..."],
  "selected_reason": "2/2 candidates produced the same result",
  "votes": 2,
  "llm_calls": 1,
  "executions": 2,
  "repairs": 0,
  "elapsed_ms": 1200,
  "error": null
}
```

## 10. 不做什么

- 不接官方 ReFoRCE；不引入其代码、Spider 2.0 目录、Snowflake/BigQuery/spaCy/GPU。
- 不使用 LangChain / LangGraph。
- 不做评测、不做 gold SQL 对比、不做指标统计。
- 不连接真实 Oracle/PostgreSQL；SQLite 只用于本地执行验证。
- 不做复杂 schema linking；全量 schema（13 张表）放进 prompt。
- 不做多轮澄清；问题已经是 B 澄清后的完整问题。
- 不做 LLM judge；选择只用结果哈希 + 静态分。

## 11. 风险与取舍

| 风险 | 处理 |
|---|---|
| 简单正则误判字符串里的 `delete` | MVP 接受；后续可换 `sqlglot` |
| 结果哈希只采样前 50 行，可能碰撞 | 同时带 `truncated` 和行数；验收时人工抽查 |
| 复杂度启发式不准 | 宁可多生成一条；`max_candidates` 可手动覆盖 |
| 合成数据 join 结果可能为空 | `_id` 列用固定范围；空结果不当作错误 |
| provider 不支持 `n` | `llm.chat` 自动循环调用 |
| 修复预算被用完 | 返回最后一次 SQL + 错误，不假装成功 |
| SQLite 与 Oracle 方言不同 | 本轮只做 SQLite；Oracle 后续迁移 |
