"""Run the fixed question set against a running backend.

    python scripts/check_questions.py           # print what actually happened
    python scripts/check_questions.py --check   # also compare with expectations

Expectations mirror ``docs/TEST_QUESTIONS.md``; update both together.
Needs: backend on :8000, ``build_db`` already run, ``LLM_API_KEY`` in ``.env``.
"""

from __future__ import annotations

import argparse
import sys

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000"
BASE = DEFAULT_BASE

# (question, max clarification rounds, expected metric, expected status)
CASES = [
    ("2025年1月急诊人次", 0, "emergency_visits", "ok"),
    ("上个月门诊人次是多少", 2, "outpatient_visits", "ok"),
    ("上个月外科住院患者的手术例数", 3, "total_surgeries", "ok"),
    ("2025年1月住院总收入", 1, "inpatient_total_cost", "ok"),
    ("2025年1月费用构成", 1, "cost_composition", "ok"),
    ("药占比是多少", 0, "outpatient_drug_ratio", "ok"),
    ("2024年1月急诊人次", 0, "emergency_visits", "ok"),
    ("2024年1月各科室门诊人次", 2, "outpatient_visits", "ok"),
    ("今天门诊人次", 1, "outpatient_visits", "ok"),
    ("你好", 0, None, "ok"),
]


def ask(client: httpx.Client, session_id: str, question: str) -> tuple[dict, list[str]]:
    response = client.post(
        f"{BASE}/queries", json={"session_id": session_id, "question": question}
    )
    response.raise_for_status()
    payload = response.json()
    codes: list[str] = []
    while payload["status"] == "needs_clarification":
        card = payload["clarification"]
        codes.append(card["code"])
        answered = client.post(
            f"{BASE}/queries/{payload['query_id']}/answers",
            json={
                "answer": {
                    "question_id": card["question_id"],
                    "option_id": card["default_option_id"],
                }
            },
        )
        answered.raise_for_status()
        payload = answered.json()
    return payload, codes


def metric_of(payload: dict) -> str | None:
    for item in payload.get("trace") or []:
        if item.get("node") == "analyze_question":
            return item.get("selected_metric_id")
    return None


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="与期望比对，不一致则退出码 1")
    parser.add_argument("--base", default=DEFAULT_BASE, help="后端地址，默认 %(default)s")
    args = parser.parse_args()
    BASE = args.base
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    failures = 0
    with httpx.Client(timeout=240) as client:
        session_id = client.post(f"{BASE}/sessions", json={}).json()["session_id"]
        for question, max_rounds, metric, status in CASES:
            payload, codes = ask(client, session_id, question)
            if payload["status"] == "error":
                # LLM 偶发失败（超时/限流），重试一次再判定
                print(f"{question:<24} 首次 error，重试一次")
                payload, codes = ask(client, session_id, question)
            actual = (payload["status"], metric_of(payload), len(codes))
            print(
                f"{question:<24} rounds={codes} status={payload['status']} "
                f"metric={metric_of(payload)} empty={payload['sql_result_empty']} "
                f"conf={payload['sql_confidence']} "
                f"err={(payload.get('error') or {}).get('code')}"
            )
            print(f"{'':<24} sql={(payload.get('sql') or '')[:100]}")
            if args.check and not (
                actual[0] == status and actual[1] == metric and actual[2] <= max_rounds
            ):
                failures += 1
                print(f"{'':<24} !! 期望 status={status} metric={metric} rounds<={max_rounds}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
