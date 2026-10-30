"""Core orchestration: evaluate -> generate -> execute -> repair -> select.

Pure Python and dependency-injected: ``llm`` and ``executor`` are callables
passed in by the caller (real clients in production, fakes in tests).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.tools.sql_agent.executor import ExecutionResult
from app.tools.sql_agent.llm import LLMError
from app.tools.sql_agent.safety import check_sql
from app.tools.sql_agent.selector import Candidate, result_hash, select

COMPLEX_WORDS = ("排名", "占比", "比例", "同比", "环比", "平均", "各", "每", "趋势", "top")
JOIN_WORDS = ("和", "与", "对比", "分别")


@dataclass
class GenerationResult:
    sql: str | None
    verified: bool
    candidates: list[Candidate]
    reason: str = ""
    result_empty: bool = False
    error: str = ""
    stats: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sql": self.sql,
            "verified": self.verified,
            "reason": self.reason,
            "result_empty": self.result_empty,
            "error": self.error,
            "stats": self.stats,
            "candidates": [
                {
                    "sql": candidate.sql,
                    "safe": candidate.safe,
                    "safety_reason": candidate.safety_reason,
                    "exec_ok": candidate.exec_ok,
                    "row_count": candidate.row_count,
                    "truncated": candidate.truncated,
                    "error": candidate.error,
                    "repaired": candidate.repaired,
                    "result_hash": candidate.result_hash,
                }
                for candidate in self.candidates
            ],
            "trace": self.trace,
        }


def estimate_n(question: str) -> int:
    text = question or ""
    if any(word in text.lower() for word in COMPLEX_WORDS):
        return 2
    if any(word in text for word in JOIN_WORDS) or len(text) > 12:
        return 2
    return 1


def generate_sql(
    question: str,
    schema_context: str,
    *,
    llm,
    executor,
    examples: list[dict] | None = None,
    schema_links: list[dict] | None = None,
    n: int | None = None,
    llm_evaluate: bool = False,
    max_candidates: int = 3,
    max_repair_rounds: int = 2,
    allowed_tables: list[str] | None = None,
) -> GenerationResult:
    started = time.perf_counter()
    stats = {"llm_calls": 0, "executions": 0, "repairs": 0}
    trace: list[dict] = []

    wanted = n if n is not None else _decide_n(question, llm, llm_evaluate)
    wanted = max(1, min(wanted, max_candidates))
    trace.append({"node": "evaluate", "n": wanted})

    try:
        raw = llm.generate_candidates(
            question,
            schema_context,
            examples=examples,
            n=wanted,
            schema_links=schema_links,
        )
    except LLMError as exc:
        return _failed(str(exc), stats, trace, started)
    stats["llm_calls"] += 1

    candidates, seen = _prepare(raw, allowed_tables)
    trace.append({"node": "generate", "candidates": len(candidates)})
    if not candidates:
        return _failed("empty_sql", stats, trace, started)

    _execute_all(candidates, executor, stats)
    trace.append(
        {
            "node": "execute",
            "ok": sum(1 for candidate in candidates if candidate.exec_ok),
            "total": len(candidates),
        }
    )

    budget = max_repair_rounds
    repairs = 0
    for candidate in list(candidates):
        if budget <= 0:
            break
        if not candidate.safe or candidate.exec_ok:
            continue
        budget -= 1
        repairs += 1
        stats["repairs"] += 1
        try:
            fixed = llm.repair(
                question,
                candidate.sql,
                candidate.error,
                schema_context,
                examples=examples,
            )
        except LLMError:
            break
        stats["llm_calls"] += 1
        prepared, seen = _prepare([fixed], allowed_tables, seen=seen)
        prepared = [item for item in prepared if item.safe]
        if not prepared:
            continue
        for item in prepared:
            item.repaired = True
        _execute_all(prepared, executor, stats)
        candidates.extend(prepared)
    if repairs:
        trace.append({"node": "repair", "repairs": repairs})

    selection = select(candidates, allow_escalation=wanted >= 2)
    if selection.needs_escalation:
        trace.append({"node": "select", "action": "escalate"})
        try:
            extra_raw = llm.generate_candidates(
                question,
                schema_context,
                examples=examples,
                n=1,
                schema_links=schema_links,
            )
            stats["llm_calls"] += 1
            extra, seen = _prepare(extra_raw, allowed_tables, seen=seen)
            if extra:
                _execute_all(extra, executor, stats)
                candidates.extend(extra)
        except LLMError:
            pass
        selection = select(candidates, allow_escalation=False)

    stats["elapsed_ms"] = _elapsed_ms(started)
    trace.append(
        {
            "node": "select",
            "verified": selection.verified,
            "reason": selection.reason,
        }
    )
    if selection.chosen is None:
        return GenerationResult(
            sql=None,
            verified=False,
            candidates=candidates,
            reason=selection.reason,
            error="sql_no_valid_candidate",
            stats=stats,
            trace=trace,
        )
    return GenerationResult(
        sql=selection.chosen.sql,
        verified=selection.verified,
        candidates=candidates,
        reason=selection.reason,
        result_empty=selection.empty,
        stats=stats,
        trace=trace,
    )


def _decide_n(question: str, llm, use_llm: bool) -> int:
    if use_llm:
        try:
            value = llm.evaluate(question)
        except (LLMError, AttributeError):
            value = None
        if value in (1, 2):
            return value
    return estimate_n(question)


def _prepare(
    raw: list[dict],
    allowed_tables: list[str] | None,
    *,
    seen: set[str] | None = None,
) -> tuple[list[Candidate], set[str]]:
    seen = set() if seen is None else seen
    prepared: list[Candidate] = []
    for item in raw:
        sql = str(item.get("sql") or "").strip()
        key = _normalize(sql)
        if sql and key in seen:
            continue
        if sql:
            seen.add(key)
        check = check_sql(sql, allowed_tables=allowed_tables)
        prepared.append(
            Candidate(
                sql=sql,
                assumptions=list(item.get("assumptions") or []),
                confidence=item.get("confidence"),
                safe=check.safe,
                safety_reason=check.reason,
            )
        )
    return prepared, seen


def _execute_all(candidates: list[Candidate], executor, stats: dict) -> None:
    for candidate in candidates:
        if not candidate.safe:
            continue
        result = executor(candidate.sql)
        stats["executions"] += 1
        _apply(candidate, result)


def _apply(candidate: Candidate, result: ExecutionResult) -> None:
    candidate.exec_ok = result.ok
    candidate.row_count = result.row_count
    candidate.truncated = result.truncated
    candidate.error = result.error
    candidate.rows = result.rows
    candidate.result_hash = result_hash(result.rows, result.truncated)


def _normalize(sql: str) -> str:
    return " ".join((sql or "").split()).rstrip(";").lower()


def _failed(message: str, stats: dict, trace: list[dict], started: float) -> GenerationResult:
    stats["elapsed_ms"] = _elapsed_ms(started)
    return GenerationResult(
        sql=None,
        verified=False,
        candidates=[],
        reason=message,
        error=message,
        stats=stats,
        trace=trace,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
