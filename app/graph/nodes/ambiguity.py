from app.graph.state import GraphState

# Hard ceiling on clarification turns, independent of configuration: a broken
# rule set must never keep asking forever.
HARD_TURN_LIMIT = 5


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

    # Off by default: these items are never asked (non-blocking) and never
    # reach the SQL prompt, so the call only costs latency.
    if getattr(deps.settings, "ambiguity_llm_supplement", False):
        matched = [item["code"] for item in items]
        items.extend(deps.llm.supplement_ambiguities(question, matched))

    pending = _pending(state, items)
    # A budget, not the number of questions: one answer can settle several
    # codes and ``final_ambiguity_check`` may surface new ones afterwards.
    # Tying it to ``len(pending)`` made the second phase unreachable.
    max_rounds = min(
        max(0, int(getattr(deps.settings, "max_clarification_turns", 3))),
        HARD_TURN_LIMIT,
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
