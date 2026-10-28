from app.graph.state import GraphState


def run(state: GraphState) -> GraphState:
    """检测问题中的歧义（时间范围、指标口径、实体指代等）。"""
    # TODO: LLM + 规则混合歧义检测
    return {
        **state,
        "ambiguity": {"has_ambiguity": False, "items": []},
    }
