# 医疗 NL2SQL 知识层

知识层负责把医疗业务事实变成 Agent 可检索的结构化知识：

- 数据库模式、表关系、时间语义、枚举、PII；
- 35 个业务指标口径；
- 术语/同义词；
- 歧义规则；
- SQLite few-shot 示例。

## 1. 目录

```text
data/
├── schema.json
├── metrics.json
├── gold_sql.json
├── glossary.json
├── ambiguities.json
└── sql_examples_sqlite.json

app/knowledge/
├── __init__.py
├── models.py              # dataclass 模型
├── loader.py              # 加载 6 个 JSON + 版本/引用校验
├── schema_catalog.py      # 表/列/关系/相关表/上下文
├── metric_catalog.py      # 指标检索
├── glossary.py            # 术语解析
├── ambiguity_catalog.py   # 歧义匹配/渲染/排序
├── example_catalog.py     # SQL 示例检索
├── context_builder.py     # 拼 prompt 上下文
├── provider.py            # 对 B 的 5 个方法
├── validate.py            # 数据校验 CLI
└── inspect.py             # 演示 CLI
```

## 2. 快速验证

```bash
python -m app.knowledge.validate --data-dir data
python -m app.knowledge.inspect "上个月心内科门诊医保患者的次均费用是多少"
```

期望：

- validate 输出 `知识库校验通过`；
- inspect 打印术语、指标候选、歧义问题、示例、相关表、join 路径、上下文长度。

## 3. 数据规模

| 文件 | 内容 |
|---|---|
| `schema.json` | 13 张表、791 列、关系图谱 |
| `metrics.json` | 35 个指标（门急诊 16、住院 12、手术 7） |
| `gold_sql.json` | 35 条 = 29 SQL + 6 公式 |
| `glossary.json` | 85 条术语 + 4 条 `needs_value_dictionary` |
| `ambiguities.json` | 8 条规则 |
| `sql_examples_sqlite.json` | 15 条 SQLite 示例 |

## 4. Provider 接口

```python
from app.knowledge.loader import load_knowledge
from app.knowledge.provider import FileKnowledgeProvider

bundle = load_knowledge("data")
provider = FileKnowledgeProvider(bundle)

provider.get_schema_context(question, intent=None)   # SchemaContext
provider.search_metrics(question, top_k=5)           # list[MetricInfo]
provider.resolve_terms(question)                     # list[TermResolution]
provider.detect_ambiguities(question, terms, asked_codes=None)  # AmbiguityReport
provider.search_sql_examples(question, top_k=3)      # list[SQLExample]
```

返回对象定义在 `app/knowledge/models.py`：

- `SchemaContext`：`tables`、`columns`、`join_paths`、`prompt_text`、`char_count`
- `MetricInfo`：`id/name/aliases/definition/formula/gold_sql_ref/time_field/...`
- `TermResolution`：`term/canonical_term/target_type/target/match/confidence/source`
- `AmbiguityReport`：`items`、`has_blocking`
- `SQLExample`：`id/question/sql/tables/pattern`

## 5. 各脚本职责

| 脚本 | 作用 |
|---|---|
| `models.py` | 6 个 JSON 的 dataclass；忽略未知字段，避免 TypeError |
| `loader.py` | 加载 6 个文件，校验版本、gold 引用、glossary target、ambiguity 列、example 表 |
| `schema_catalog.py` | 查表/列/关系；选相关表；输出 join 路径和 prompt 上下文 |
| `metric_catalog.py` | 按名称/别名/定义检索指标；返回完整口径 |
| `glossary.py` | 解析术语；支持 enum/in/contains/time_range/metric；科室名低置信度推断 |
| `ambiguity_catalog.py` | 命中规则、渲染 `{term}`、过滤 `asked_codes`、按 priority 排序 |
| `example_catalog.py` | 按指标/表/文本重叠/pattern 检索 SQL 示例 |
| `context_builder.py` | 选相关表，拼 schema + join + 术语 + 指标 + 示例，控制在 8k 字符 |
| `provider.py` | B 的唯一入口，5 个方法 |
| `validate.py` | 数量、版本、引用、关系列、示例表校验 |
| `inspect.py` | 命令行演示/排障 |

## 6. 扩展方式

### 新增指标

1. 在 `data/metrics.json` 加一条，必须有 `id/name/aliases/definition/formula/gold_sql_ref/related_tables/time_field/unit`。
2. 在 `data/gold_sql.json` 加对应 SQL 或公式条目。
3. 必要时在 `data/glossary.json` 加别名术语。
4. 运行 `python -m app.knowledge.validate --data-dir data`。

### 新增术语

在 `data/glossary.json` 的 `terms` 加：

```json
{
  "term": "门诊",
  "aliases": ["门诊患者"],
  "target_type": "enum",
  "match": "enum",
  "target": {"table": "visit", "column": "activity_type_code", "value": "1"},
  "source": "schema.enum",
  "confidence": "high"
}
```

### 新增歧义规则

在 `data/ambiguities.json` 加 `code/category/trigger_terms/question/options/default/blocking/priority/related_columns/related_metrics`。

### 新增 SQL 示例

在 `data/sql_examples_sqlite.json` 加 `id/source_gold_id/question/sql/tables/pattern/notes`，单条 ≤20 行，必须能在 SQLite 执行。

## 7. `needs_value_dictionary`

`glossary.json` 里的 `needs_value_dictionary` 是**待补真实值的清单**：

- 科室具体名称；
- 诊断名称；
- 检查项目名称；
- 医保类型细分标签。

这些值不在 schema 里，不能凭空编。拿到真实值或数据字典后：

1. 在 `terms` 里加具体术语；
2. 从 `needs_value_dictionary` 删除对应条目；
3. 重新跑 validate。

## 8. 边界

- 不 import `app.graph` / `app.api`；
- 不连接真实数据库；
- 不做 SQL 执行；
- 不生成 `gold_sql_sqlite.json` / `sql_examples.json`（当前阶段暂不做）。
