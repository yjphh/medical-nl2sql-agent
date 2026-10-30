from datetime import date, timedelta

from app.graph.state import GraphState

SLOT_LABELS = {
    "time_field_scope": "统计时间字段",
    "patient_scope": "患者范围",
    "aggregation": "统计方式",
    "fee_type": "费用口径",
}

# Codes that already have a dedicated sentence in ``run``.
DEDICATED_CODES = {
    "time_range",
    "metric_scope",
    "visit_type_scope",
    "department_entity",
    "insurance_scope",
}


def _time_range(label: str, reference: str) -> str | None:
    try:
        ref = date.fromisoformat(reference)
    except ValueError:
        return None
    if label == "自然月":
        first = ref.replace(day=1)
        end = first
        start = (first - timedelta(days=1)).replace(day=1)
        return f"{start.isoformat()} 至 {end.isoformat()}（不含结束日）"
    if label == "最近30天":
        start = ref - timedelta(days=30)
        return f"{start.isoformat()} 至 {ref.isoformat()}（不含结束日）"
    return None


def _schema_links(context: dict) -> list[dict]:
    """Flatten ``SchemaContext`` into ``[{table, columns, relationship}]``.

    Built from the context ``finalize`` already computed, so no extra
    knowledge-layer call is needed.
    """
    tables = context.get("tables") or []
    columns = context.get("columns") or {}
    paths = context.get("join_paths") or []
    links = []
    for table in tables:
        related = [
            f"{path.get('from_table')}→{path.get('to_table')}"
            for path in paths
            if table in (path.get("from_table"), path.get("to_table"))
        ]
        links.append({
            "table": table,
            "columns": list(columns.get(table, [])),
            "relationship": "；".join(related),
        })
    return links


def _department_token(state: GraphState) -> str | None:
    for term in state.get("terms", []):
        target = term.get("target", {})
        if target.get("table") == "visit" and target.get("column") == "dept_name":
            return target.get("value") or term.get("term")
    return None


def run(state: GraphState, deps) -> dict:
    original = state["original_question"]
    slots = state.get("resolved_slots", {})
    extras: list[str] = []

    if "time_range" in slots:
        rendered = _time_range(
            slots["time_range"],
            getattr(deps.settings, "reference_date", "2025-12-31"),
        )
        if rendered:
            extras.append(f"时间口径：{slots['time_range']}（{rendered}）")

    selected_id = state.get("selected_metric_id")
    selected = next(
        (m for m in state.get("metrics", []) if m.get("id") == selected_id),
        None,
    )
    if selected:
        extras.append(f"指标：{selected.get('name')}")

    if "visit_type_scope" in slots:
        extras.append(f"就诊类型：{slots['visit_type_scope']}")

    if "department_entity" in slots:
        token = _department_token(state)
        if token:
            extras.append(f"科室：{slots['department_entity']}‘{token}’")

    if "insurance_scope" in slots:
        extras.append(f"医保范围：{slots['insurance_scope']}")

    # Any other answered slot still has to reach the SQL prompt, otherwise the
    # answer is silently dropped (the SQL would use whatever the model prefers).
    for code, label in SLOT_LABELS.items():
        if code in DEDICATED_CODES or code not in slots:
            continue
        extras.append(f"{label}：{slots[code]}")

    rewritten = original
    if extras:
        rewritten = f"{original}\n补充条件：" + "；".join(extras) + "。"

    context = deps.knowledge.get_schema_context(rewritten)
    context_dict = context.to_dict()
    return {
        "rewritten_question": rewritten,
        "schema_context": context_dict,
        "schema_links": _schema_links(context_dict),
        "trace": [{
            "node": "finalize_question",
            "rewritten_question": rewritten,
        }],
    }


def check(state: GraphState, deps) -> dict:
    rewritten = state.get("rewritten_question") or state["original_question"]
    terms = deps.knowledge.resolve_terms(rewritten)
    report = deps.knowledge.detect_ambiguities(rewritten, terms)

    known = {item.get("code") for item in state.get("ambiguities", [])}
    asked = set(state.get("asked_codes", []))
    resolved = set(state.get("resolved_codes", []))
    new_items = [
        item for item in report.items
        if item.get("blocking")
        and item.get("code") not in known
        and item.get("code") not in asked
        and item.get("code") not in resolved
    ]

    max_rounds = state.get("max_rounds", 0)
    if new_items and state.get("clarification_round", 0) < max_rounds:
        merged = [*state.get("ambiguities", []), *new_items]
        return {
            "ambiguities": merged,
            "pending_codes": [item["code"] for item in new_items],
            "final_ambiguities": new_items,
            "trace": [{
                "node": "final_ambiguity_check",
                "new_codes": [item["code"] for item in new_items],
            }],
        }

    # Budget exhausted (or no new code): fall back to the rule defaults so the
    # SQL prompt still shows which interpretation was assumed.
    assumptions = list(state.get("assumptions", []))
    known = {a.get("code") for a in assumptions}
    added: list[str] = []
    for item in new_items:
        if item.get("code") in known:
            continue
        default = item.get("default", "")
        if not default:
            continue
        assumptions.append({
            "code": item["code"],
            "value": default,
            "label": default,
            "source": "default",
            "reason": "max_rounds",
        })
        added.append(f"{item.get('code')}：{default}")

    update: dict = {
        "final_ambiguities": [],
        "assumptions": assumptions,
        "trace": [{
            "node": "final_ambiguity_check",
            "new_codes": [item["code"] for item in new_items],
            "unresolved": len(new_items),
        }],
    }
    # A default that is only listed in ``assumptions`` never reaches the model:
    # it would guess the same ambiguity again, differently.
    if added:
        rewritten = state.get("rewritten_question") or state["original_question"]
        update["rewritten_question"] = (
            f"{rewritten}\n未澄清项按默认口径：" + "；".join(added) + "。"
        )
    return update
