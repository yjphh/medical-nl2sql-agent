"""Prompt builders for the SQLite SQL agent.

The agent normally receives ``schema_context.prompt_text`` from the graph.  The
renderer here is the fallback used by tests and the CLI when no context is
passed.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "data" / "schema.json"
DEFAULT_EXAMPLES_PATH = Path(__file__).resolve().parents[3] / "data" / "sql_examples_sqlite.json"

SQLITE_SYSTEM_PROMPT = """你是医院数据仓库的 SQLite SQL 生成助手。生成的 SQL 会被直接执行，必须严格可运行。

方言与结构：
- 只生成一条 SELECT 或 WITH ... SELECT；禁止 INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA/ATTACH。
- 只使用 SQLite 语法：IFNULL/COALESCE、strftime、||、LIMIT、CASE WHEN。
- 禁止 NVL、TO_CHAR、ROWNUM、DATE_FORMAT、反引号等 Oracle/MySQL 写法。
- 表名、列名必须来自给定 schema，使用小写 snake_case，不要编造。
- 时间列以 TEXT 存储，格式为 'YYYY-MM-DD HH:MM:SS' 或 'YYYY-MM-DD'，直接用字符串比较，
  例如 col >= '2025-01-01' AND col < '2025-02-01'。
- 标注 [PII] 的列不得出现在 SELECT 输出；只允许放在 COUNT(DISTINCT ...) 里。
- 事实表若有 modify_flag，查询时过滤 modify_flag IN ('1','2')。
- 多表查询必须显式写 JOIN ... ON ...，使用 schema 给出的关系，禁止笛卡尔积。
- 口径不确定时采用最合理的默认并写进 assumptions；sql 不允许为 null 或空字符串。

输出：
- 只输出 JSON，不要 markdown 代码块，不要额外解释。"""


def load_schema(path: str | Path = DEFAULT_SCHEMA_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_examples(path: str | Path = DEFAULT_EXAMPLES_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["examples"]


def schema_text(schema: dict, tables: list[str] | None = None, max_chars: int = 8000) -> str:
    """Render the schema, giving every table a share of ``max_chars``."""
    selected = [t for t in schema["tables"] if not tables or t["name"] in tables]
    headers = []
    for table in selected:
        header = f"表 {table['name']}"
        if table.get("description"):
            header += f"（{table['description']}）"
        primary_key = ",".join(table.get("primary_key") or []) or "无"
        headers.append(f"{header} 主键={primary_key}")
        for relation in table.get("relationships") or []:
            headers.append(f"  关系: {relation.get('on')} ({relation.get('type')})")

    header_text = "\n".join(headers)
    remaining = max_chars - len(header_text)
    if remaining <= 0:
        return header_text[:max_chars]
    share = max(200, remaining // max(1, len(selected)))

    blocks = []
    for table in selected:
        lines = [f"# {table['name']} 的列:"]
        used = 0
        omitted = 0
        for column in _ordered_columns(table):
            line = _column_line(column)
            if used + len(line) + 1 > share:
                omitted += 1
                continue
            lines.append(line)
            used += len(line) + 1
        if omitted:
            lines.append(f"  ... 其余 {omitted} 列从略")
        blocks.append("\n".join(lines))

    text = header_text + "\n\n" + "\n\n".join(blocks)
    return text if len(text) <= max_chars else text[:max_chars] + "\n...（已截断）"


_MONEY_HINTS = ("cost", "fee", "amount", "price", "charge", "income", "revenue", "balance", "pay")


def _ordered_columns(table: dict) -> list[dict]:
    """Rank columns so truncation keeps the ones generators need most.

    Order: primary key, join keys, enum columns, date/time and money columns,
    PII markers, then everything else in schema order.
    """
    primary_key = {name.lower() for name in table.get("primary_key") or []}
    join_keys = set()
    for relation in table.get("relationships") or []:
        for token in str(relation.get("on", "")).replace("=", " ").split():
            if "." in token:
                join_keys.add(token.split(".", 1)[1].lower())

    def rank(column: dict) -> int:
        name = column["name"].lower()
        kind = column["type"].upper()
        if name in primary_key:
            return 0
        if name in join_keys:
            return 1
        if column.get("enum_values"):
            return 2
        if "DATE" in kind or "TIME" in kind or any(hint in name for hint in _MONEY_HINTS):
            return 3
        if column.get("is_pii"):
            return 4
        return 5

    return sorted(table["columns"], key=rank)


def _column_line(column: dict) -> str:
    line = f"  - {column['name']} {column['type']}"
    flags = []
    if column.get("is_pii"):
        flags.append("PII")
    enums = column.get("enum_values") or []
    if enums:
        values = "/".join(str(item.get("value")) for item in enums[:6])
        flags.append(f"枚举: {values}")
    if flags:
        line += " [" + "; ".join(flags) + "]"
    return line


def render_examples(examples: list[dict], limit: int = 3) -> str:
    blocks = []
    for example in examples[:limit]:
        blocks.append(f"-- {example.get('question', '')}\n{example.get('sql', '')}")
    return "\n\n".join(blocks)


def build_evaluate_messages(question: str) -> list[dict]:
    instruction = (
        "判断这个问题的 SQL 生成难度。简单问题（单表、单一过滤）返回 n=1；"
        "涉及排名、占比、同比环比、多表 join 或分组的问题返回 n=2。\n"
        f"问题：{question}\n"
        '只输出 JSON：{"complexity": "simple|complex", "n": 1}'
    )
    return [
        {"role": "system", "content": "你是医院数据查询的难度评估器，只输出 JSON。"},
        {"role": "user", "content": instruction},
    ]


def build_generate_messages(
    question: str,
    schema_context: str,
    *,
    examples: list[dict] | None = None,
    n: int = 1,
    schema_links: list[dict] | None = None,
    repair: dict | None = None,
) -> list[dict]:
    parts = [f"数据库 schema：\n{schema_context}"]
    if schema_links:
        parts.append("模式链接（仅作提示，可能不完整）：\n" + json.dumps(schema_links, ensure_ascii=False))
    if examples:
        parts.append("参考示例：\n" + render_examples(examples))
    if repair:
        parts.append(
            "上一次候选执行失败，请修正后重新生成：\n"
            f"SQL: {repair.get('sql', '')}\n"
            f"错误: {repair.get('error', '')}"
        )
    parts.append(f"问题：{question}")
    parts.append(f"请生成 {n} 条语义等价、写法不同的候选 SQL。")
    parts.append(
        '只输出 JSON：{"candidates": [{"sql": "...", "assumptions": ["..."], '
        '"confidence": 0.0}]}'
    )
    return [
        {"role": "system", "content": SQLITE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def build_repair_messages(
    question: str,
    failed_sql: str,
    error: str,
    schema_context: str,
    *,
    examples: list[dict] | None = None,
) -> list[dict]:
    parts = [f"数据库 schema：\n{schema_context}"]
    if examples:
        parts.append("参考示例：\n" + render_examples(examples))
    parts.append(f"问题：{question}")
    parts.append(f"下面这条 SQL 执行失败：\n{failed_sql}\n错误信息：{error}")
    parts.append("请修正为一条可执行的 SQLite SELECT。")
    parts.append('只输出 JSON：{"sql": "...", "assumptions": ["..."]}')
    return [
        {"role": "system", "content": SQLITE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
