"""Pretty-print generated SQL for display only.

The model usually answers with one long line, which forces horizontal scrolling
in ``st.code``.  Formatting happens in the front end so the backend contract
(and the exact SQL that was verified on the mirror) stays untouched.
"""

from __future__ import annotations

import re

# Longest phrases first: "LEFT JOIN" must win over "JOIN".
_BREAK_BEFORE = (
    "LEFT JOIN",
    "RIGHT JOIN",
    "INNER JOIN",
    "FULL JOIN",
    "GROUP BY",
    "ORDER BY",
    "UNION ALL",
    "JOIN",
    "FROM",
    "WHERE",
    "HAVING",
    "LIMIT",
    "UNION",
)


def format_sql(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        return ""
    try:
        import sqlglot

        return sqlglot.parse_one(text, dialect="sqlite").sql(pretty=True, dialect="sqlite")
    except Exception:  # noqa: BLE001 - display must never break on odd SQL
        return _fallback(text)


def _fallback(text: str) -> str:
    """Keyword-per-line fallback when sqlglot cannot parse the statement.

    One pass with an ordered alternation, so ``LEFT JOIN`` is matched as a
    whole before the bare ``JOIN`` alternative can split it.
    """
    out = re.sub(r"\s+", " ", text).strip()
    keywords = "|".join(re.escape(keyword) for keyword in _BREAK_BEFORE)
    pattern = re.compile(rf"(?i)\s+({keywords})\b")
    return pattern.sub(lambda match: "\n" + match.group(1), out)
