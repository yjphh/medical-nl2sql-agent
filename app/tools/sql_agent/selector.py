"""Candidate model, result hashing, static scoring and consensus selection.

``Candidate`` lives here so both ``core`` and the selector helpers can share it
without a circular import.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Candidate:
    sql: str
    assumptions: list[str] = field(default_factory=list)
    confidence: float | None = None
    safe: bool = False
    safety_reason: str = ""
    exec_ok: bool = False
    row_count: int = 0
    truncated: bool = False
    error: str = ""
    repaired: bool = False
    result_hash: str = ""
    # Rows never leave the process; excluded from repr so logs stay PII-free.
    rows: list[tuple] = field(default_factory=list, repr=False)


@dataclass
class Selection:
    chosen: Candidate | None
    verified: bool
    reason: str
    empty: bool = False
    needs_escalation: bool = False


def result_hash(rows: list[tuple], truncated: bool = False) -> str:
    """Hash the result set, ignoring column aliases and row order."""
    normalized = sorted(
        tuple("" if value is None else str(value) for value in row) for row in rows
    )
    payload = json.dumps([normalized, truncated], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def static_score(candidate: Candidate) -> tuple:
    """Fallback ranking when candidates disagree: safer, then simpler SQL."""
    joins = candidate.sql.upper().count(" JOIN ")
    return (
        candidate.safe,
        candidate.exec_ok,
        -joins,
        -len(candidate.sql),
        candidate.confidence or 0.0,
    )


def select(candidates: list[Candidate], *, allow_escalation: bool) -> Selection:
    valid = [candidate for candidate in candidates if candidate.safe and candidate.exec_ok]
    if not valid:
        return Selection(chosen=None, verified=False, reason="没有可执行的候选")

    if len(valid) == 1:
        only = valid[0]
        return Selection(
            chosen=only,
            verified=True,
            reason="仅 1 条有效候选",
            empty=only.row_count == 0,
        )

    counts = Counter(candidate.result_hash for candidate in valid)
    top_hash, top_count = counts.most_common(1)[0]
    if top_count > 1 and top_count * 2 > len(valid):
        group = [candidate for candidate in valid if candidate.result_hash == top_hash]
        best = max(group, key=static_score)
        reason = (
            f"{len(valid)} 条候选结果一致"
            if top_count == len(valid)
            else f"{top_count}/{len(valid)} 条候选结果一致"
        )
        return Selection(
            chosen=best,
            verified=True,
            reason=reason,
            empty=best.row_count == 0,
        )

    if allow_escalation:
        return Selection(
            chosen=None,
            verified=False,
            reason=f"{len(valid)} 条候选结果不一致，需要升级",
            needs_escalation=True,
        )

    best = max(valid, key=static_score)
    return Selection(
        chosen=best,
        verified=False,
        reason=f"{len(valid)} 条候选结果不一致，无共识，按静态分选优",
        empty=best.row_count == 0,
    )
