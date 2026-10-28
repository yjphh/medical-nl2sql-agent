import json
import os

class FileKnowledgeProvider:
    def __init__(self, data_dir="data"):
        self.data_dir = data_dir
        self.metrics_data = self._load_json("metrics.json")
        self.schema_data = self._load_json("schema.json")
        self.glossary_data = self._load_json("glossary.json")
        self.ambiguities_data = self._load_json("ambiguities.json")
        self.examples_data = self._load_json("sql_examples_sqlite.json")

    def _load_json(self, filename):
        path = os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            print(f"⚠️ 警告: 找不到文件 {path}")
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 1. 找指标
    def search_metrics(self, question, top_k=5):
        results = []
        for m in self.metrics_data.get("metrics", []):
            # 极其简单的关键词匹配：如果指标名在问题里
            if m["name"] in question or any(alias in question for alias in m.get("aliases", [])):
                results.append(m)
        return results[:top_k]

    # 2. 翻译黑话
    def resolve_terms(self, question):
        resolved = []
        for t in self.glossary_data.get("terms", []):
            if t["term"] in question or any(alias in question for alias in t.get("aliases", [])):
                resolved.append(t)
        return resolved

    # 3. 找歧义
    def detect_ambiguities(self, question, terms=None):
        detected = []
        for rule in self.ambiguities_data.get("rules", []):
            for trigger in rule.get("trigger_terms", []):
                if trigger in question:
                    detected.append(rule)
                    break
        return detected

    # 4. 找相似 SQL 示例
    def search_sql_examples(self, question, top_k=2):
        results = []
        for ex in self.examples_data.get("examples", []):
            # 简单的匹配：问题里的字出现在示例问题里
            if any(char in ex["question"] for char in question if len(char) > 0):
                results.append(ex)
        return results[:top_k]

    # 5. 拼装给大模型的上下文
    def get_schema_context(self, question, intent=None):
        # 简化版：直接返回核心表的 Schema
        context = "数据库表结构如下：\n"
        for table in self.schema_data.get("tables", []):
            context += f"表名: {table['name']} ({table.get('description', '')})\n"
            for col in table.get("columns", []):
                context += f"  - {col['name']} ({col.get('type', '')}): {col.get('description', '')}\n"
        return context