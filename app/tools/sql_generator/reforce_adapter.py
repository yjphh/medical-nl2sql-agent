from typing import Any

from app.tools.sql_generator.base import BaseSQLGenerator


class ReforceSQLGenerator(BaseSQLGenerator):
    """Reforce 风格 SQL 生成适配器（预留外部引擎接入点）。"""

    def __init__(self, model: str = "", api_key: str = "", base_url: str = "") -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def generate(
        self,
        question: str,
        schema_context: str,
        intent: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        # TODO: 接入 reforce/LLM 推理服务
        raise NotImplementedError("ReforceSQLGenerator not implemented yet")
