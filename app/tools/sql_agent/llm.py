"""OpenAI-compatible chat client for the SQLite SQL agent.

The client only builds prompts and parses JSON; all decisions (voting,
repair budget) live in ``core`` so the flow stays testable with fakes.
"""

from __future__ import annotations

import json

from app.tools.sql_agent import prompt


class LLMError(RuntimeError):
    pass


class OpenAICompatLLM:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.7,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.temperature = temperature
        self.timeout = timeout
        self._client = None

    def generate_candidates(
        self,
        question: str,
        schema_context: str,
        *,
        examples: list[dict] | None = None,
        n: int = 1,
        schema_links: list[dict] | None = None,
        repair: dict | None = None,
    ) -> list[dict]:
        messages = prompt.build_generate_messages(
            question,
            schema_context,
            examples=examples,
            n=n,
            schema_links=schema_links,
            repair=repair,
        )
        data = _parse_json(self._complete(messages, temperature=self.temperature))
        items = data.get("candidates")
        if not isinstance(items, list):
            items = [data] if data.get("sql") else []
        return [item for item in items if isinstance(item, dict)]

    def repair(
        self,
        question: str,
        sql: str,
        error: str,
        schema_context: str,
        *,
        examples: list[dict] | None = None,
    ) -> dict:
        messages = prompt.build_repair_messages(
            question, sql, error, schema_context, examples=examples
        )
        return _parse_json(self._complete(messages, temperature=0.0))

    def evaluate(self, question: str) -> int | None:
        messages = prompt.build_evaluate_messages(question)
        data = _parse_json(self._complete(messages, temperature=0.0))
        value = data.get("n")
        return value if value in (1, 2) else None

    def _complete(self, messages: list[dict], *, temperature: float) -> str:
        response = self._get_client().chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def _get_client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMError("missing_api_key: 请在 .env 配置 LLM_API_KEY")
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client


def _parse_json(text: str) -> dict:
    content = (text or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"invalid_json: {content[:200]}")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid_json: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("invalid_json: expected an object")
    return data
