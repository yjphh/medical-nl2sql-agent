from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    ambiguity,
    answer_merger,
    clarification,
    intent_schema,
    sql_generation,
    validation,
)
from app.graph.routing import route_after_ambiguity, route_after_validation
from app.graph.state import GraphState


def build_graph() -> StateGraph:
    g: StateGraph = StateGraph(GraphState)

    g.add_node("intent_schema", intent_schema.run)
    g.add_node("ambiguity", ambiguity.run)
    g.add_node("clarification", clarification.run)
    g.add_node("sql_generation", sql_generation.run)
    g.add_node("validation", validation.run)
    g.add_node("answer_merger", answer_merger.run)

    g.add_edge(START, "intent_schema")
    g.add_edge("intent_schema", "ambiguity")
    g.add_conditional_edges(
        "ambiguity",
        route_after_ambiguity,
        {"clarification": "clarification", "sql_generation": "sql_generation"},
    )
    g.add_conditional_edges(
        "validation",
        route_after_validation,
        {"answer_merger": "answer_merger", "clarification": "clarification", "error": END},
    )
    g.add_edge("sql_generation", "validation")
    g.add_edge("answer_merger", END)
    g.add_edge("clarification", END)

    return g.compile()
