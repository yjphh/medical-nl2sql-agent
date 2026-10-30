from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph import nodes, routing
from app.graph.state import GraphState
from app.graph.subgraphs.clarification import build_clarification_subgraph
from app.memory.checkpointer import get_checkpointer


def build_graph(deps):
    graph = StateGraph(GraphState)
    graph.add_node("analyze", partial(nodes.analyze.run, deps=deps))
    graph.add_node("ambiguity_policy", partial(nodes.ambiguity.run, deps=deps))
    graph.add_node("clarification", build_clarification_subgraph(deps))
    graph.add_node("finalize_question", partial(nodes.finalize.run, deps=deps))
    graph.add_node("final_ambiguity_check", partial(nodes.finalize.check, deps=deps))
    graph.add_node("generate_sql", partial(nodes.sql_generation.run, deps=deps))

    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "ambiguity_policy")
    graph.add_conditional_edges(
        "ambiguity_policy",
        routing.route_after_ambiguity,
        {"clarification": "clarification", "finalize": "finalize_question"},
    )
    graph.add_edge("clarification", "finalize_question")
    graph.add_edge("finalize_question", "final_ambiguity_check")
    graph.add_conditional_edges(
        "final_ambiguity_check",
        routing.route_after_final_check,
        {"clarification": "clarification", "generate_sql": "generate_sql"},
    )
    graph.add_edge("generate_sql", END)
    return graph.compile(checkpointer=get_checkpointer())
