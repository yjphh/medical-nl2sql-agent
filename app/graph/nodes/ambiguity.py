from app.graph.state import GraphState


def _pending(state: GraphState, items: list[dict]) -> list[dict]:
    asked = set(state.get("asked_codes", []))
    resolved = set(state.get("resolved_codes", []))
    pending = [
        item for item in items
        if item.get("blocking")
        and item.get("code") not in asked
        and item.get("code") not in resolved
    ]
    pending.sort(key=lambda item: (item.get("priority", 99), item.get("code", "")))
    return pending


def run(state: GraphState, deps) -> dict:
    question = state["original_question"]
    items = list(state.get("ambiguities", []))

    if getattr(deps.settings, "ambiguity_llm_supplement", True):
        matched = [item["code"] for item in items]
        items.extend(deps.llm.supplement_ambiguities(question, matched))

    pending = _pending(state, items)
    max_rounds = min(
        getattr(deps.settings, "max_clarification_turns", 3),
        len(pending),
    )
    update = {
        "ambiguities": items,
        "pending_codes": [item["code"] for item in pending],
        "max_rounds": max_rounds,
        "trace": [{
            "node": "ambiguity_policy",
            "pending_codes": [item["code"] for item in pending],
            "max_rounds": max_rounds,
        }],
    }
    # Set here rather than inside the clarification subgraph: writes made by a
    # subgraph only reach the parent state once the subgraph finishes, but the
    # API has to answer while the subgraph is paused on ``interrupt``.
    if pending:
        update["status"] = "needs_clarification"
    return update
