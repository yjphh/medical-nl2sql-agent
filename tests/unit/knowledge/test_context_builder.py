"""The prompt must describe the dialect we execute, not the warehouse's origin."""

from app.knowledge.provider import FileKnowledgeProvider


def _context(question: str = "2025年1月门诊人次"):
    return FileKnowledgeProvider("data").get_schema_context(question)


def test_prompt_declares_the_executable_dialect():
    context = _context()
    assert "可执行方言: sqlite" in context.prompt_text
    # The bare "- 方言: oracle" line used to be copied from schema.json and
    # contradicted the SQLite generator prompt.
    assert "- 方言: oracle" not in context.prompt_text


def test_prompt_lists_sqlite_syntax_rules():
    prompt = _context().prompt_text
    assert "strftime" in prompt
    assert "TO_CHAR" in prompt  # named only as forbidden


def test_prompt_still_carries_the_warehouse_dialect_as_context():
    prompt = _context().prompt_text
    assert "数据仓库原始方言: oracle" in prompt
    assert "本轮不生成" in prompt
