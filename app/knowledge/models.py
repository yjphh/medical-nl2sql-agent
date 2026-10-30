"""Dataclasses for the medical knowledge layer.

They deliberately ignore unknown JSON keys so schema changes do not break
loading with ``TypeError`` again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


def _filtered(cls, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in names}


@dataclass
class ColumnInfo:
    name: str
    type: str = "VARCHAR"
    description: str = ""
    nullable: bool = True
    aliases: list[str] = field(default_factory=list)
    time_role: str | None = None
    enum_values: list[dict[str, Any]] = field(default_factory=list)
    is_pii: bool = False
    pii_policy: str | None = None
    dictionary: str | None = None
    is_required: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ColumnInfo:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipInfo:
    to_table: str
    on: str
    type: str = "many_to_one"
    note: str = ""
    source: str = ""
    confidence: str = "medium"
    fanout: str = "none"
    filter: str | None = None
    name: str | None = None
    from_table: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationshipInfo:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TableInfo:
    name: str
    description: str = ""
    grain: str | None = None
    primary_key: list[str] = field(default_factory=list)
    columns: list[ColumnInfo] = field(default_factory=list)
    relationships: list[RelationshipInfo] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TableInfo:
        payload = _filtered(cls, data)
        payload["columns"] = [ColumnInfo.from_dict(c) for c in data.get("columns", [])]
        payload["relationships"] = [
            RelationshipInfo.from_dict(r) for r in data.get("relationships", [])
        ]
        return cls(**payload)

    def column(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MetricInfo:
    id: str
    name: str
    category: str = ""
    aliases: list[str] = field(default_factory=list)
    definition: str = ""
    formula: str = ""
    gold_sql_ref: str = ""
    related_tables: list[str] = field(default_factory=list)
    time_field: str = ""
    default_interpretation: str = ""
    ambiguity_notes: list[str] = field(default_factory=list)
    unit: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MetricInfo:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TermInfo:
    term: str
    aliases: list[str] = field(default_factory=list)
    target_type: str = "column"
    target: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    confidence: str = "medium"
    match: str = ""
    values: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TermInfo:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AmbiguityRule:
    code: str
    category: str = ""
    trigger_terms: list[str] = field(default_factory=list)
    question: str = ""
    options: list[str] = field(default_factory=list)
    default: str = ""
    blocking: bool = True
    priority: int = 50
    related_columns: list[str] = field(default_factory=list)
    related_metrics: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AmbiguityRule:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SQLExample:
    id: str
    source_gold_id: str = ""
    question: str = ""
    sql: str = ""
    tables: list[str] = field(default_factory=list)
    pattern: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SQLExample:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoldItem:
    id: str
    metric_id: str
    metric_name: str
    kind: str
    sql: str = ""
    formula: str = ""
    tables: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldItem:
        return cls(**_filtered(cls, data))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeBundle:
    data_dir: str
    schema_version: str
    dialect: str
    tables: list[TableInfo]
    metrics: list[MetricInfo]
    gold_items: list[GoldItem]
    terms: list[TermInfo]
    needs_value_dictionary: list[dict[str, Any]]
    ambiguities: list[AmbiguityRule]
    examples: list[SQLExample]
    versions: dict[str, str] = field(default_factory=dict)

    def table_map(self) -> dict[str, TableInfo]:
        return {t.name: t for t in self.tables}

    def metric_map(self) -> dict[str, MetricInfo]:
        return {m.id: m for m in self.metrics}


@dataclass
class TermResolution:
    term: str
    canonical_term: str
    target_type: str
    target: dict[str, Any]
    confidence: str = "medium"
    source: str = ""
    match: str = ""
    values: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SchemaContext:
    tables: list[str]
    columns: dict[str, list[str]]
    join_paths: list[dict[str, Any]]
    prompt_text: str
    char_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AmbiguityReport:
    items: list[dict[str, Any]]
    has_blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
