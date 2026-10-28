# 医疗 NL2SQL 知识层（Knowledge Layer）启动文档

本模块负责将医疗数据库的表结构、业务指标、术语映射及 SQL 示例整理成标准 JSON 文件，并提供统一的 Python 接口供 Agent 编排层调用。

## 1. 环境依赖

运行本模块代码需要安装以下依赖（推荐使用 Python 3.10 或 3.11）：

```bash
pip install pandas openpyxl
```

## 2. 目录结构说明

```text
medical-nl2sql-agent/
├── data/                        # 知识层数据文件夹（由本模块生成）
│   ├── metrics.json             # 35 个核心业务指标及公式
│   ├── gold_sql.json            # 29 条原始标准 SQL
│   ├── schema.json              # 数据库表结构（当前包含核心的 visit 表）
│   ├── glossary.json            # 业务黑话/术语映射表
│   ├── ambiguities.json         # 歧义触发词及追问规则
│   └── sql_examples_sqlite.json # 适配 SQLite 的 few-shot SQL 示例
│
└── app/knowledge/               # 知识层代码
    ├── __init__.py
    ├── provider.py              # 对 (编排层) 暴露的统一接口
    └── inspect.py               # 命令行调试与演示脚本
```

## 3. 如何验证并启动

请确保当前终端位于项目**根目录**（即 `medical-nl2sql-agent`），执行以下命令：

```bash
python -m app.knowledge.inspect "上个月门诊人次是多少"
```

**预期输出：**
系统会打印出识别的术语（如“门诊”）、匹配的指标、检测到的歧义追问、参考的 SQLite 示例，以及拼接好的数据库 Schema 上下文。

## 4. 供编排层调用的接口

 无需关心底层 JSON 是如何读取的，只需实例化 `FileKnowledgeProvider` 即可调用以下方法：

```python
from app.knowledge.provider import FileKnowledgeProvider

# 初始化（指定 data 所在目录）
provider = FileKnowledgeProvider(data_dir="data")

# 1. 获取用于生成 SQL 的 Schema 上下文
schema_ctx = provider.get_schema_context(question)

# 2. 搜索相关业务指标
metrics = provider.search_metrics(question, top_k=5)

# 3. 术语（黑话）翻译
terms = provider.resolve_terms(question)

# 4. 检测歧义并返回追问话术
ambiguities = provider.detect_ambiguities(question, terms)

# 5. 搜索最相似的 SQL 示例
examples = provider.search_sql_examples(question, top_k=3)
```

## 5. 后续维护指南

*   **新增指标**：直接编辑 `data/metrics.json`，按照现有字典格式增加一条记录，保持 ID 唯一。
*   **新增术语**：直接编辑 `data/glossary.json`，将用户的俗称映射到具体表字段。
*   **新增表结构**：将 DDL 的表字段补充到 `data/schema.json` 中。