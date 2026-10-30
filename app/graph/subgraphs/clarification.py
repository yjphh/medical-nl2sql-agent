from functools import partial

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.state import GraphState

POLISH_CODES = {
    "department_entity",
    "metric_scope",
    "insurance_scope",
    "time_field_scope",
}

OPTION_IDS = {
    "department_entity": {"精确匹配": "exact", "名称包含": "contains"},
    "visit_type_scope": {"仅门诊": "outpatient_only", "门诊+急诊": "outpatient_emergency"},
    "time_range": {"自然月": "natural_month", "最近30天": "last_30_days"},
    "insurance_scope": {"全部医保": "all", "职工医保": "employee", "居民医保": "resident"},
    "aggregation": {"总量": "total", "平均值": "average", "去重计数": "distinct", "排名": "rank"},
    "time_field_scope": {
        "就诊时间": "visit_time",
        "入院时间": "admit_time",
        "出院时间": "discharge_time",
        "结算时间": "settle_time",
        "费用发生时间": "cost_time",
    },
}


def _option_id(code: str, label: str, index: int) -> str:
    return OPTION_IDS.get(code, {}).get(label, f"opt_{index}")


def _options_for(state: GraphState, item: dict) -> tuple[list[dict], str]:
    code = item["code"]
    if code == "metric_scope" and state.get("metrics"):
        options = [
            {"id": m["id"], "label": m["name"], "value": m["id"]}
            for m in state["metrics"]
        ]
        default = state.get("selected_metric_id") or options[0]["id"]
        return options, default

    labels = item.get("options", [])
    options = [
        {"id": _option_id(code, label, i), "label": label, "value": label}
        for i, label in enumerate(labels)
    ]
    default_label = item.get("default", "")
    default = next(
        (o["id"] for o in options if o["label"] == default_label),
        options[0]["id"] if options else "default",
    )
    return options, default


def pending_items(state: GraphState) -> list[dict]:
    """Blocking items that were not asked yet and are not already resolved.

    Recomputed after every answer, because one answer can resolve several
    codes (``visit_type_scope`` also settles ``metric_scope``), so the initial
    ``pending_codes`` list is stale the moment the user replies.
    """
    asked = set(state.get("asked_codes", []))
    resolved = set(state.get("resolved_codes", []))
    items = [
        item for item in state.get("ambiguities", [])
        if item.get("blocking")
        and item.get("code") not in asked
        and item.get("code") not in resolved
    ]
    items.sort(key=lambda item: (item.get("priority", 99), item.get("code", "")))
    return items


def plan(state: GraphState, deps) -> dict:
    remaining = pending_items(state)
    if not remaining:
        return {"pending_question": None, "pending_codes": []}
    item = remaining[0]
    code = item["code"]
    round_no = state.get("clarification_round", 0) + 1
    # ``max_rounds`` in state is the turn budget; the card should still say
    # "1/2" when only two questions are actually going to be asked.
    budget = state.get("max_rounds", 3)

    options, default_option_id = _options_for(state, item)
    pending = {
        "id": f"q_{code}",
        "code": code,
        "matched_term": item.get("matched_term", ""),
        "question": item.get("question", ""),
        "options": options,
        "default_option_id": default_option_id,
        "allow_free_text": True,
        "can_skip": True,
        "round": round_no,
        "max_rounds": min(budget, round_no + len(remaining) - 1),
    }

    if code in POLISH_CODES and getattr(deps.settings, "clarify_llm_polish", True):
        polished = deps.llm.polish_clarification(pending)
        if polished:
            pending["question"] = polished["question"]
            by_id = {o["id"]: o for o in pending["options"]}
            for option in polished["options"]:
                if option["id"] in by_id:
                    by_id[option["id"]]["label"] = option["label"]
            pending["options"] = list(by_id.values())

    return {
        "pending_question": pending,
        "pending_codes": [i["code"] for i in remaining],
        "status": "needs_clarification",
        "trace": [{
            "node": "clarify_plan",
            "code": code,
            "round": pending["round"],
        }],
    }


def ask(state: GraphState) -> dict:
    answer = interrupt(state["pending_question"])
    return {"last_answer": answer}


def apply(state: GraphState) -> dict:
    question = state.get("pending_question") or {}
    answer = state.get("last_answer") or {}
    options = {o["id"]: o for o in question.get("options", [])}
    default_id = question.get("default_option_id")

    answer_id = answer.get("option_id")
    if answer.get("question_id") and answer["question_id"] != question.get("id"):
        answer_id = None
    free_text = str(answer.get("text") or "").strip()

    selected = None
    source = "user"
    if answer_id in options:
        selected = options[answer_id]
    elif free_text:
        selected = {"id": None, "label": free_text, "value": free_text}
    else:
        selected = options.get(default_id) or next(iter(options.values()), None)
        source = "default"

    if selected is None:
        return {"status": "error", "error": {"code": "clarification_no_option"}}

    code = question["code"]
    value = selected["value"]
    label = selected["label"]
    round_no = state.get("clarification_round", 0) + 1
    slots = {**state.get("resolved_slots", {}), code: value}
    assumptions = list(state.get("assumptions", []))
    if source == "default":
        assumptions.append({
            "code": code,
            "value": value,
            "label": label,
            "source": "default",
        })

    update = {
        "resolved_slots": slots,
        "resolved_codes": [*state.get("resolved_codes", []), code],
        "asked_codes": [*state.get("asked_codes", []), code],
        "clarification_round": round_no,
        "assumptions": assumptions,
        "clarification_history": [*state.get("clarification_history", []), {
            "round": round_no,
            "code": code,
            "question": question.get("question", ""),
            "answer": answer,
            "resolved": True,
            "source": source,
        }],
        "pending_question": None,
        "last_answer": None,
        "trace": [{
            "node": "clarify_apply",
            "code": code,
            "value": value,
            "source": source,
            "round": round_no,
        }],
    }
    if code == "metric_scope":
        update["selected_metric_id"] = value
    return update


def route_more(state: GraphState) -> str:
    if state.get("clarification_round", 0) >= state.get("max_rounds", 3):
        return "done"
    return "plan" if pending_items(state) else "done"


def build_clarification_subgraph(deps):
    graph = StateGraph(GraphState)
    graph.add_node("plan", partial(plan, deps=deps))
    graph.add_node("ask", ask)
    graph.add_node("apply", apply)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "ask")
    graph.add_edge("ask", "apply")
    graph.add_conditional_edges("apply", route_more, {"plan": "plan", "done": END})
    return graph.compile()
