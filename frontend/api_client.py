"""The only HTTP exit of the frontend.

No Streamlit import here on purpose: this module is unit-tested against
``httpx.MockTransport`` and could be reused by another UI later.
"""

from __future__ import annotations

from typing import Any, Self

import httpx

from frontend.models import Metric, QueryResponse


class BackendError(RuntimeError):
    """Raised for connection errors and non-2xx responses."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class BackendClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(0, f"无法连接后端（{self.base_url}）：{exc}") from exc
        if response.status_code >= 400:
            raise BackendError(response.status_code, _detail(response))
        if not response.content:
            return {}
        return response.json()

    def health(self, timeout: float = 2.0) -> bool:
        try:
            data = self._request("GET", "/health", timeout=timeout)
        except BackendError:
            return False
        return data.get("status") == "ok"

    def create_session(self, user_id: str | None = None) -> str:
        payload = {"user_id": user_id} if user_id else {}
        data = self._request("POST", "/sessions", json=payload)
        session_id = data.get("session_id")
        if not session_id:
            raise BackendError(500, "后端没有返回 session_id")
        return str(session_id)

    def list_metrics(self, q: str = "", top_k: int = 50) -> list[Metric]:
        params: dict[str, Any] = {"top_k": top_k}
        if q:
            params["q"] = q
        data = self._request("GET", "/metrics", params=params)
        return [Metric.from_dict(item) for item in data.get("items") or []]

    def ask(self, session_id: str, question: str) -> QueryResponse:
        data = self._request(
            "POST",
            "/queries",
            json={"session_id": session_id, "question": question},
        )
        return QueryResponse.from_dict(data)

    def answer(
        self,
        query_id: str,
        *,
        question_id: str | None = None,
        option_id: str | None = None,
        text: str | None = None,
    ) -> QueryResponse:
        answer: dict[str, Any] = {}
        if question_id:
            answer["question_id"] = question_id
        if option_id:
            answer["option_id"] = option_id
        if text:
            answer["text"] = text
        data = self._request(
            "POST",
            f"/queries/{query_id}/answers",
            json={"answer": answer},
        )
        return QueryResponse.from_dict(data)

    def get_query(self, query_id: str) -> dict[str, Any]:
        return self._request("GET", f"/queries/{query_id}")


def _detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:200]}"
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail or data)
