from app.graph.state import GraphState


def route_after_ambiguity(state: GraphState) -> str:
    """歧义检测后路由：有歧义则澄清，否则生成 SQL。"""
    if state.get("status") == "needs_clarification" or (state.get("ambiguity") or {}).get("has_ambiguity"):
        return "clarification"
    return "sql_generation"


def route_after_validation(state: GraphState) -> str:
    """SQL 校验后路由：校验失败可澄清或报错。"""
    validation = state.get("validation") or {}
    if validation.get("valid"):
        return "answer_merger"
    if validation.get("needs_clarification"):
        return "clarification"
    return "error"
