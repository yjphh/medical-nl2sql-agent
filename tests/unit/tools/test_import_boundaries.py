import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "app" / "tools" / "sql_agent"
FORBIDDEN = ("langgraph", "langchain", "app.graph", "app.api")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_sql_agent_does_not_depend_on_graph_or_langchain():
    offenders = {}
    for path in PACKAGE.glob("*.py"):
        bad = sorted(
            name for name in _imports(path) if name.startswith(FORBIDDEN)
        )
        if bad:
            offenders[path.name] = bad
    assert not offenders, offenders
