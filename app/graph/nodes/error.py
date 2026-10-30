from app.graph.state import GraphState


def run(state: GraphState) -> dict:
    return {
        "status": "error",
        "error": state.get("error") or {"code": "unknown"},
    }
