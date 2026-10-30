"""Schema lookup, relationship traversal, and prompt-context rendering."""

from __future__ import annotations

from app.knowledge.models import (
    MetricInfo,
    SQLExample,
    TableInfo,
    TermResolution,
)

MONEY_HINTS = ("cost", "fee", "amount", "price", "charge", "income", "revenue", "balance", "pay")


class SchemaCatalog:
    def __init__(self, tables: list[TableInfo], dialect: str = "oracle") -> None:
        self.tables = tables
        self.dialect = dialect
        self._map = {t.name: t for t in tables}
        self._neighbors: dict[str, set[str]] = {t.name: set() for t in tables}
        for table in tables:
            for rel in table.relationships:
                if rel.to_table in self._map:
                    self._neighbors[table.name].add(rel.to_table)
                    self._neighbors[rel.to_table].add(table.name)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def get_table(self, name: str) -> TableInfo | None:
        return self._map.get(name)

    def get_column(self, table_name: str, column_name: str):
        table = self._map.get(table_name)
        return table.column(column_name) if table else None

    def find_tables(
        self,
        terms: list[TermResolution] | None = None,
        metrics: list[MetricInfo] | None = None,
        examples: list[SQLExample] | None = None,
        max_tables: int = 6,
    ) -> list[str]:
        selected: list[str] = []

        def add(name: str | None) -> None:
            if name and name in self._map and name not in selected:
                selected.append(name)

        for term in terms or []:
            target = term.target or {}
            add(target.get("table"))
        for metric in metrics or []:
            for name in metric.related_tables:
                add(name)

        # Examples are only a fallback for table selection; otherwise a
        # tangential few-shot can pull in unrelated tables and blow up the
        # prompt.
        if not selected:
            for example in examples or []:
                for name in example.tables:
                    add(name)

        if not selected:
            for name in ("visit", "patient", "medical_expense_rec"):
                add(name)

        return selected[:max_tables]

    def join_paths(self, table_names: list[str]) -> list[dict]:
        selected = set(table_names)
        paths: list[dict] = []
        for table in self.tables:
            if table.name not in selected:
                continue
            for rel in table.relationships:
                if rel.to_table not in selected:
                    continue
                item = {
                    "from_table": table.name,
                    "to_table": rel.to_table,
                    "on": rel.on,
                    "type": rel.type,
                    "note": rel.note,
                    "fanout": rel.fanout,
                    "filter": rel.filter,
                    "source": rel.source,
                    "confidence": rel.confidence,
                }
                sides = [s.strip() for s in rel.on.split("=")]
                if len(sides) == 2 and "." in sides[0] and "." in sides[1]:
                    item["from_column"] = sides[0].split(".", 1)[1]
                    item["to_column"] = sides[1].split(".", 1)[1]
                paths.append(item)
        return paths

    def selected_columns(
        self,
        table_names: list[str],
        focus_columns: set[str] | None = None,
        max_columns_per_table: int = 25,
    ) -> dict[str, list[str]]:
        focus_columns = focus_columns or set()
        join_columns: set[str] = set()
        for path in self.join_paths(table_names):
            ft, fc = path.get("from_table"), path.get("from_column")
            tt, tc = path.get("to_table"), path.get("to_column")
            if ft and fc:
                join_columns.add(f"{ft}.{fc}")
            if tt and tc:
                join_columns.add(f"{tt}.{tc}")

        result: dict[str, list[str]] = {}
        for name in table_names:
            table = self._map.get(name)
            if not table:
                continue
            pk = set(table.primary_key)

            def priority(col, table=table, pk=pk) -> tuple[int, int]:
                key = f"{table.name}.{col.name}"
                if col.name in pk or key in join_columns or key in focus_columns:
                    return (0, 0)
                if col.time_role:
                    return (1, 0)
                if col.enum_values:
                    return (2, 0)
                # Money columns carry the metric formula (total_cost /
                # outpatient_cost / hospitalization_total_cost); ranking PII
                # first used to push them out of the 25-column budget.
                if any(hint in col.name.lower() for hint in MONEY_HINTS):
                    return (3, 0)
                if col.is_pii:
                    return (5, 0)
                return (4, 0)

            ordered = sorted(
                enumerate(table.columns),
                key=lambda pair: (priority(pair[1]), pair[0]),
            )
            result[name] = [c.name for _, c in ordered[:max_columns_per_table]]
        return result

    def _column_line(self, col) -> str:
        parts = [f"- {col.name} {col.type}"]
        if col.description:
            parts.append(col.description[:80])
        if col.time_role:
            parts.append(f"时间语义: {col.time_role}")
        if col.enum_values:
            values = ", ".join(
                f"{v.get('value')}={v.get('label')}" for v in col.enum_values[:10]
            )
            parts.append(f"枚举: {values}")
        if col.is_pii:
            parts.append("[PII 禁止输出]")
        return "；".join(parts)

    def to_prompt_context(
        self,
        table_names: list[str] | None = None,
        focus_columns: set[str] | None = None,
        max_chars: int = 8000,
        max_columns_per_table: int = 25,
    ) -> str:
        names = table_names or self.table_names
        columns = self.selected_columns(
            names, focus_columns, max_columns_per_table=max_columns_per_table
        )
        lines: list[str] = ["## 相关表"]
        for name in names:
            table = self._map.get(name)
            if not table:
                continue
            lines.append(f"\n### {name}（{table.description}）")
            if table.grain:
                lines.append(f"粒度: {table.grain}")
            for col_name in columns.get(name, []):
                col = table.column(col_name)
                if col is None:
                    continue
                line = self._column_line(col)
                if sum(len(x) + 1 for x in lines) + len(line) > max_chars:
                    lines.append("- …（列已截断，避免超出上下文预算）")
                    return "\n".join(lines)
                lines.append(line)
            paths = self.join_paths([name]) if len(names) == 1 else []
            for path in paths:
                lines.append(
                    f"关系: {path['on']}（{path['type']}"
                    f"{'，扇出' if path.get('fanout') == 'to_many' else ''}）"
                )
        if names:
            all_paths = self.join_paths(names)
            if all_paths:
                lines.append("\n## 关联关系")
                for path in all_paths:
                    extra = f"；{path['note']}" if path.get("note") else ""
                    filt = f"；过滤: {path['filter']}" if path.get("filter") else ""
                    lines.append(
                        f"- {path['on']}（{path['type']}"
                        f"{'，扇出' if path.get('fanout') == 'to_many' else ''}{extra}{filt}）"
                    )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n…（已截断）"
        return text
