"""DeepSeek client used by the graph nodes.

The graph depends on this small protocol instead of ChatDeepSeek directly, so
tests can inject a deterministic fake.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from app.prompts.ambiguity import build_ambiguity_prompt
from app.prompts.analyze import build_analyze_prompt
from app.prompts.base import SYSTEM_PROMPT
from app.prompts.clarify import build_clarify_prompt
from app.prompts.sql import build_sql_prompt


@dataclass
class SQLResult:
    sql: str | None = None
    assumptions: list[str] = field(default_factory=list)
    confidence: float | None = None
    error: str | None = None


class LLMClient(Protocol):
    def choose_metric(self, question: str, candidates: list, context: str) -> str | None: ...

    def polish_clarification(self, payload: dict[str, Any]) -> dict[str, Any] | None: ...

    def supplement_ambiguities(self, question: str, matched_codes: list[str]) -> list[dict]: ...

    def generate_sql(self, question: str, context: str, metrics: list, examples: list) -> SQLResult: ...


def _extract_json(text: str) -> dict | list:
    text = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class DeepSeekLLMClient:
    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        temperature: float = 0.2,
    ) -> None:
        self._model = model
        self._api_key = api_key or None
        self._base_url = base_url or None
        self._temperature = temperature
        self._llm: ChatDeepSeek | None = None

    @property
    def llm(self) -> ChatDeepSeek:
        # Built lazily so importing the app (or running tests) never requires
        # DEEPSEEK_API_KEY.
        if self._llm is None:
            self._llm = ChatDeepSeek(
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
                temperature=self._temperature,
            )
        return self._llm

    def _invoke(self, user_prompt: str) -> str:
        try:
            result = self.llm.invoke([
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])
            content = result.content
            return content if isinstance(content, str) else str(content)
        except Exception:  # noqa: BLE001 - degrade instead of failing the request
            return ""

    def choose_metric(self, question: str, candidates: list, context: str) -> str | None:
        if len(candidates) <= 1:
            return candidates[0].id if candidates else None
        payload = [
            {
                "id": m.id,
                "name": m.name,
                "aliases": m.aliases,
                "definition": m.definition,
            }
            for m in candidates
        ]
        raw = self._invoke(build_analyze_prompt(question, payload, context))
        try:
            data = _extract_json(raw)
            metric_id = data.get("metric_id") if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None
        return metric_id if metric_id in {m.id for m in candidates} else None

    def polish_clarification(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw = self._invoke(build_clarify_prompt(payload))
        try:
            data = _extract_json(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        question = data.get("question")
        options = data.get("options")
        if not isinstance(question, str) or not isinstance(options, list):
            return None
        original = {o["id"]: o for o in payload.get("options", [])}
        merged = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            oid = opt.get("id")
            if oid not in original:
                continue
            merged.append({
                "id": oid,
                "label": str(opt.get("label") or original[oid]["label"]),
                "value": original[oid]["value"],
            })
        if len(merged) != len(original):
            return None
        return {"question": question, "options": merged}

    def supplement_ambiguities(self, question: str, matched_codes: list[str]) -> list[dict]:
        raw = self._invoke(build_ambiguity_prompt(question, matched_codes))
        try:
            data = _extract_json(raw)
        except (ValueError, TypeError):
            return []
        items = data.get("items", []) if isinstance(data, dict) else []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if not code or code in matched_codes:
                continue
            result.append({
                "code": code,
                "category": item.get("category", "semantic"),
                "matched_term": item.get("term", ""),
                "question": item.get("question", ""),
                "options": item.get("options", []),
                "default": item.get("default", ""),
                "blocking": False,
                "priority": item.get("priority", 90),
                "related_columns": [],
                "related_metrics": [],
                "source": "llm",
            })
        return result

    def generate_sql(self, question: str, context: str, metrics: list, examples: list) -> SQLResult:
        raw = self._invoke(build_sql_prompt(question, context, metrics, examples))
        if not raw:
            return SQLResult(error="llm_unavailable")
        try:
            data = _extract_json(raw)
        except (ValueError, TypeError) as exc:
            return SQLResult(error=f"invalid_json: {exc}")
        if not isinstance(data, dict):
            return SQLResult(error="invalid_response")
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return SQLResult(
                assumptions=list(data.get("assumptions", [])),
                error="empty_sql",
            )
        return SQLResult(
            sql=sql.strip().rstrip(";"),
            assumptions=[str(x) for x in data.get("assumptions", [])],
            confidence=data.get("confidence"),
        )
