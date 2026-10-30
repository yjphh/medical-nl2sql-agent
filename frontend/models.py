"""Typed views of the backend responses.

Kept independent from Streamlit so ``api_client`` and its tests can import
them without a running UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Option:
    id: str
    label: str
    value: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Option:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            value=str(data.get("value", "")),
        )


@dataclass
class Clarification:
    question_id: str
    code: str
    question: str
    options: list[Option] = field(default_factory=list)
    default_option_id: str | None = None
    round: int = 1
    max_rounds: int = 1
    allow_free_text: bool = True
    can_skip: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Clarification:
        return cls(
            question_id=str(data.get("question_id", "")),
            code=str(data.get("code", "")),
            question=str(data.get("question", "")),
            options=[Option.from_dict(item) for item in data.get("options") or []],
            default_option_id=data.get("default_option_id"),
            round=int(data.get("round") or 1),
            max_rounds=int(data.get("max_rounds") or 1),
            allow_free_text=bool(data.get("allow_free_text", True)),
            can_skip=bool(data.get("can_skip", True)),
        )

    def option_label(self, option_id: str | None) -> str:
        for option in self.options:
            if option.id == option_id:
                return option.label
        return ""


@dataclass
class Assumption:
    code: str
    label: str = ""
    value: str = ""
    source: str = "default"
    reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Assumption:
        return cls(
            code=str(data.get("code", "")),
            label=str(data.get("label") or data.get("value") or ""),
            value=str(data.get("value", "")),
            source=str(data.get("source", "default")),
            reason=data.get("reason"),
        )


@dataclass
class Metric:
    id: str
    name: str
    category: str = ""
    aliases: list[str] = field(default_factory=list)
    definition: str = ""
    unit: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Metric:
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            aliases=[str(a) for a in data.get("aliases") or []],
            definition=str(data.get("definition", "")),
            unit=str(data.get("unit", "")),
        )


@dataclass
class QueryResponse:
    session_id: str
    query_id: str
    status: str
    sql: str | None = None
    sql_source: str | None = None
    sql_confidence: float | None = None
    sql_result_empty: bool = False
    clarification: Clarification | None = None
    assumptions: list[Assumption] = field(default_factory=list)
    clarification_history: list[dict[str, Any]] = field(default_factory=list)
    message: str | None = None
    error: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryResponse:
        raw_clarification = data.get("clarification")
        return cls(
            session_id=str(data.get("session_id", "")),
            query_id=str(data.get("query_id", "")),
            status=str(data.get("status", "error")),
            sql=data.get("sql"),
            sql_source=data.get("sql_source"),
            sql_confidence=data.get("sql_confidence"),
            sql_result_empty=bool(data.get("sql_result_empty", False)),
            clarification=(
                Clarification.from_dict(raw_clarification)
                if isinstance(raw_clarification, dict)
                else None
            ),
            assumptions=[
                Assumption.from_dict(item) for item in data.get("assumptions") or []
            ],
            clarification_history=list(data.get("clarification_history") or []),
            message=data.get("message"),
            error=data.get("error"),
            trace=list(data.get("trace") or []),
        )

    @property
    def is_waiting(self) -> bool:
        return self.status == "needs_clarification" and self.clarification is not None
