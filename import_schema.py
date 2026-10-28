#!/usr/bin/env python
"""从数据库 introspect schema 并导出为 data/schema.json。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


def export_schema_sqlalchemy(database_url: str) -> dict:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(database_url)
    inspector = inspect(engine)
    tables = []
    for table_name in inspector.get_table_names():
        columns = []
        for col in inspector.get_columns(table_name):
            columns.append(
                {
                    "name": col["name"],
                    "data_type": str(col["type"]),
                    "description": "",
                    "is_pii": False,
                }
            )
        tables.append({"name": table_name, "description": "", "columns": columns})
    return {"tables": tables}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import DB schema into data/schema.json")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--output", default="data/schema.json")
    args = parser.parse_args()

    schema = export_schema_sqlalchemy(args.database_url)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Schema exported: {len(schema['tables'])} tables -> {out_path}")


if __name__ == "__main__":
    main()
