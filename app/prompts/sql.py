def build_sql_prompt(
    question: str,
    context: str,
    metrics: list,
    examples: list,
) -> str:
    metric_lines = []
    for m in metrics:
        metric_lines.append(
            f"- {m.name} ({m.id})：{m.definition}；公式={m.formula}；"
            f"时间字段={m.time_field}"
        )
    example_lines = []
    for ex in examples:
        example_lines.append(f"- {ex.question}\n  {ex.sql[:800]}")

    return "\n".join([
        "请根据下面的完整问题和数据库上下文生成一条 SQLite SQL。",
        f"问题：{question}",
        "",
        "Schema 与关系：",
        context,
        "",
        "指标口径：",
        "\n".join(metric_lines) if metric_lines else "(无)",
        "",
        "相似 SQL：",
        "\n".join(example_lines) if example_lines else "(无)",
        "",
        "要求：",
        "- 只生成一条 SELECT 或 WITH ... SELECT；",
        "- 只使用上面出现的表和列；",
        "- 不输出 PII 列；",
        "- 时间用 TEXT 'YYYY-MM-DD' 比较；",
        "- 多表必须写 JOIN 条件，注意一对多扇出；",
        "- assumptions 写清楚你采用的默认口径。",
        "",
        '只输出 JSON：{"sql": "...", "assumptions": ["..."], "confidence": 0.0}。',
        "无法生成时 sql 输出 null，并在 assumptions 里说明原因。",
    ])
