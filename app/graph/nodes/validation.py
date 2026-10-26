from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """校验 SQL：语法、只读、表/列存在性、超时保护等。"""
    # TODO: 调用 app.tools.sql_validator
    return {
        **state,
        "validation": {"valid": False, "needs_clarification": False, "reason": "not_implemented"},
    }
