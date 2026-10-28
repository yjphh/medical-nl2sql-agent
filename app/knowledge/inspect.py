import sys
import os

# 确保能找到 app 模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.knowledge.provider import FileKnowledgeProvider

def main():
    if len(sys.argv) < 2:
        print("用法: python -m app.knowledge.inspect \"你的问题\"")
        return

    question = sys.argv[1]
    print(f"🔍 正在分析问题: 【{question}】\n")

    provider = FileKnowledgeProvider(data_dir="data")

    # 1. 翻译术语
    terms = provider.resolve_terms(question)
    print("1️⃣ 术语识别:")
    for t in terms:
        print(f"   - {t['term']} -> {t['target']}")
    if not terms: print("   (未识别到术语)")

    # 2. 找指标
    metrics = provider.search_metrics(question)
    print("\n2️⃣ 指标匹配:")
    for m in metrics:
        print(f"   - {m['name']} (ID: {m['id']})")
    if not metrics: print("   (未匹配到指标)")

    # 3. 找歧义
    ambiguities = provider.detect_ambiguities(question, terms)
    print("\n3️⃣ 歧义检测 (是否需要追问):")
    for a in ambiguities:
        print(f"   - ⚠️ {a['question']}")
        print(f"     选项: {a['options']}")
    if not ambiguities: print("   (无歧义，可直接生成)")

    # 4. 找相似 SQL
    examples = provider.search_sql_examples(question)
    print("\n4️⃣ 参考 SQL 示例:")
    for e in examples:
        print(f"   - 问题: {e['question']}")
        print(f"     SQL: {e['sql'][:80]}...")
    if not examples: print("   (无相似示例)")

    # 5. 打印 schema
    print("\n5️⃣ 数据库 Schema 上下文:")
    schema_ctx = provider.get_schema_context(question)
    print("   " + schema_ctx.replace("\n", "\n   ")[:300] + "...\n")

if __name__ == "__main__":
    main()