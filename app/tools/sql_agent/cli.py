"""Command-line demo: question -> SQLite SQL -> executed on the local mirror.

Usage::

    python -m app.tools.sql_agent.cli "2025年1月门诊人次是多少"
    python -m app.tools.sql_agent.cli "上个月检查项目排名" --n 2 --show-candidates
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.tools.sql_agent.build_db import DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH
from app.tools.sql_agent.core import generate_sql
from app.tools.sql_agent.executor import sqlite_executor
from app.tools.sql_agent.llm import LLMError, OpenAICompatLLM
from app.tools.sql_agent.prompt import load_examples, load_schema, schema_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate SQLite SQL for a question")
    parser.add_argument("question")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA_PATH))
    parser.add_argument("--n", type=int, default=None, help="候选数；默认按问题难度启发式")
    parser.add_argument("--max-repair-rounds", type=int, default=2)
    parser.add_argument("--show-candidates", action="store_true")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    schema = load_schema(args.schema)
    context = schema_text(schema)
    examples = load_examples()
    allowed_tables = [table["name"] for table in schema["tables"]]

    llm = OpenAICompatLLM(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )
    executor = sqlite_executor(args.db)

    try:
        result = generate_sql(
            args.question,
            context,
            llm=llm,
            executor=executor,
            examples=examples,
            n=args.n,
            max_repair_rounds=args.max_repair_rounds,
            allowed_tables=allowed_tables,
        )
    except LLMError as exc:
        print(f"LLM 调用失败：{exc}")
        return 1

    if args.show_candidates:
        for candidate in result.candidates:
            print(
                f"  [safe={candidate.safe} exec={candidate.exec_ok} rows="
                f"{candidate.row_count} repaired={candidate.repaired}] {candidate.sql}"
            )
    print(result.sql or f"（未生成 SQL：{result.error}）")
    print(f"verified={result.verified} empty={result.result_empty} reason={result.reason}")
    print(f"stats={result.stats}")
    return 0 if result.sql else 1


if __name__ == "__main__":
    raise SystemExit(main())
