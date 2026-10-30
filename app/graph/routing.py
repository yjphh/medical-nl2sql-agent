from app.graph.state import GraphState


def route_after_ambiguity(state: GraphState) -> str:
    return "clarification" if state.get("pending_codes") else "finalize"


def route_after_final_check(state: GraphState) -> str:
    return "clarification" if state.get("final_ambiguities") else "generate_sql"
