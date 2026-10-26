from abc import ABC, abstractmethod
from typing import Any


class BaseSQLGenerator(ABC):
    """SQL 生成器抽象接口。"""

    @abstractmethod
    def generate(
        self,
        question: str,
        schema_context: str,
        intent: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """根据自然语言问题与 schema 上下文生成 SQL。"""
        raise NotImplementedError
