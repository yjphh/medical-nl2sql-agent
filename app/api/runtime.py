"""Holds the compiled graph and its dependencies for the API process.

Building them loads ``data/`` and constructs the DeepSeek client, so it is
done lazily on the first request (and can be replaced by tests).
"""

from __future__ import annotations

_GRAPH = None
_DEPS = None


def get_deps():
    global _DEPS
    if _DEPS is None:
        from app.graph.deps import build_deps

        _DEPS = build_deps()
    return _DEPS


def set_deps(deps) -> None:
    global _DEPS
    _DEPS = deps


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        from app.graph.builder import build_graph

        _GRAPH = build_graph(get_deps())
    return _GRAPH


def set_graph(graph) -> None:
    global _GRAPH
    _GRAPH = graph


def reset() -> None:
    """Drop both singletons (tests only)."""
    global _GRAPH, _DEPS
    _GRAPH = None
    _DEPS = None
