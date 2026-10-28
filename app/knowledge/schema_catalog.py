import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    description: str = ""
    is_pii: bool = False


@dataclass
class TableInfo:
    name: str
    description: str = ""
    columns: list[ColumnInfo] = field(default_factory=list)


@dataclass
class SchemaCatalog:
    tables: list[TableInfo] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "SchemaCatalog":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        tables = [
            TableInfo(
                name=t["name"],
                description=t.get("description", ""),
                columns=[ColumnInfo(**c) for c in t.get("columns", [])],
            )
            for t in raw.get("tables", [])
        ]
        return cls(tables=tables)

    def get_table(self, name: str) -> TableInfo | None:
        return next((t for t in self.tables if t.name == name), None)

    def to_prompt_context(self, limit_tables: int | None = None) -> str:
        lines: list[str] = []
        for table in (self.tables if limit_tables is None else self.tables[:limit_tables]):
            lines.append(f"## {table.name}\n{table.description}")
            cols = ", ".join(f"{c.name}:{c.data_type}" for c in table.columns)
            lines.append(f"columns: {cols}")
        return "\n\n".join(lines)
