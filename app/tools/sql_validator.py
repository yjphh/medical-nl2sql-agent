import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    needs_clarification: bool = False


class SQLValidator:
    """SQL 静态校验：只读、危险语句拦截、基础语法检查。"""

    _WRITE_PATTERN = re.compile(
        r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
        re.IGNORECASE,
    )

    def validate(self, sql: str, allowed_tables: list[str] | None = None) -> ValidationResult:
        sql = sql.strip().rstrip(";")
        if not sql:
            return ValidationResult(valid=False, reason="empty_sql")

        if not sql.upper().startswith("SELECT"):
            return ValidationResult(
                valid=False,
                reason="only SELECT statements are allowed",
                needs_clarification=True,
            )

        if self._WRITE_PATTERN.search(sql):
            return ValidationResult(valid=False, reason="write_operation_detected")

        if allowed_tables is not None:
            missing = self._find_unknown_tables(sql, allowed_tables)
            if missing:
                return ValidationResult(
                    valid=False,
                    reason=f"unknown tables: {', '.join(missing)}",
                    needs_clarification=True,
                )

        return ValidationResult(valid=True)

    @staticmethod
    def _find_unknown_tables(sql: str, allowed_tables: list[str]) -> list[str]:
        allowed = {t.lower() for t in allowed_tables}
        found = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", sql, re.IGNORECASE))
        return sorted(t for t in found if t.lower() not in allowed)
