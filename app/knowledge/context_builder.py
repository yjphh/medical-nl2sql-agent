"""Assemble the relevant schema, metrics, terms, and examples into one prompt."""

from __future__ import annotations

from app.knowledge.example_catalog import ExampleCatalog
from app.knowledge.glossary import Glossary
from app.knowledge.metric_catalog import MetricCatalog
from app.knowledge.models import (
    MetricInfo,
    SchemaContext,
    SQLExample,
    TermResolution,
)
from app.knowledge.schema_catalog import SchemaCatalog


def _focus_columns(terms: list[TermResolution], metrics: list[MetricInfo]) -> set[str]:
    focus: set[str] = set()
    for term in terms:
        target = term.target or {}
        table, column = target.get("table"), target.get("column")
        if table and column:
            focus.add(f"{table}.{column}")
    for metric in metrics:
        if metric.time_field and "." in metric.time_field:
            focus.add(metric.time_field)
    return focus


def _dialect_lines(declared: str | None, executable: str) -> list[str]:
    """State the dialect the generator may use, not the warehouse's origin.

    ``schema.json`` describes an Oracle-family warehouse, but this round only
    executes SQLite.  Telling the model "方言: oracle" invites ``TO_CHAR`` /
    ``NVL`` SQL that the SQLite parser then rejects.
    """
    lines = [f"- 可执行方言: {executable}（本地镜像）"]
    if declared and declared.lower() != executable.lower():
        lines.append(f"- 数据仓库原始方言: {declared}（本轮不生成，仅作参考）")
    if executable.lower() == "sqlite":
        lines.append(
            "- 只能使用 SQLite 语法：IFNULL/COALESCE、strftime、||、LIMIT；"
            "禁止 NVL、TO_CHAR、ROWNUM、DATE_FORMAT、反引号"
        )
    return lines


def build_context(
    question: str,
    terms: list[TermResolution],
    metrics: list[MetricInfo],
    examples: list[SQLExample],
    schema_catalog: SchemaCatalog,
    metric_catalog: MetricCatalog,
    glossary: Glossary,
    example_catalog: ExampleCatalog,
    max_chars: int = 8000,
    executable_dialect: str = "sqlite",
) -> SchemaContext:
    table_names = schema_catalog.find_tables(terms, metrics, examples)
    focus = _focus_columns(terms, metrics)
    join_paths = schema_catalog.join_paths(table_names)
    columns = schema_catalog.selected_columns(table_names, focus)

    schema_budget = int(max_chars * 0.55)
    schema_text = schema_catalog.to_prompt_context(
        table_names, focus, max_chars=schema_budget
    )
    metrics_text = metric_catalog.to_prompt_context(metrics)
    terms_text = glossary.to_prompt_context(terms)
    examples_text = example_catalog.to_prompt_context(examples)

    sections = [
        schema_text,
        "## 约束",
        "- 只生成一条只读 SELECT / WITH ... SELECT",
        *_dialect_lines(schema_catalog.dialect, executable_dialect),
        "- 不输出姓名、身份证号等 PII 列",
        "- 时间字段和指标口径不明确时，先澄清再生成",
    ]
    if terms_text:
        sections.append(terms_text)
    if metrics_text:
        sections.append(metrics_text)
    if examples_text:
        sections.append(examples_text)

    prompt = "\n\n".join(section for section in sections if section)
    if len(prompt) > max_chars:
        prompt = prompt[: max_chars - 20] + "\n…（已截断）"

    return SchemaContext(
        tables=table_names,
        columns=columns,
        join_paths=join_paths,
        prompt_text=prompt,
        char_count=len(prompt),
    )
