# knowledge/ 脚本完善计划

> 前提：`data/` 下 6 个 JSON 已补齐并校验通过。
> 本轮目标：让 `app/knowledge/` 真正能读、能查、能拼上下文、能演示。
> 不改 `data/`，不改 `app/graph/`、`app/api/`、`app/tools/`。

## 1. 现状

| 脚本 | 应该做什么 | 现在的问题 |
|---|---|---|
| `loader.py` | 加载 6 个 JSON，返回知识对象 | 只加载 schema/metrics，返回 tuple；`ColumnInfo(**c)` 报 `TypeError` |
| `schema_catalog.py` | 查表、查字段、查关系、拼 schema 上下文 | 字段定义和 `schema.json` 不匹配；直接 `TypeError`；没有关系/枚举/PII |
| `metric_catalog.py` | 按名称/别名找指标 | `MetricInfo(**m)` 遇到 `id` 等字段报 `TypeError`；不查别名 |
| `provider.py` | 对 B 暴露 5 个方法 | 绕过 loader/catalog 直接读 JSON；上下文 49,508 字符；歧义 `{term}` 未渲染；示例按单字匹配 |
| `inspect.py` | 命令行演示/排障 | Windows GBK 遇 emoji 崩溃；`{term}` 未渲染；只打印全量 schema |

缺失：

- `models.py`
- `glossary.py`
- `ambiguity_catalog.py`
- `example_catalog.py`
- `context_builder.py`
- `validate.py`

## 2. 目标结构

```text
app/knowledge/
├── __init__.py
├── models.py              # dataclass 模型 + KnowledgeBundle
├── loader.py              # 加载 6 个 JSON，版本/引用校验
├── schema_catalog.py      # 表/列/关系/相关表选择/上下文
├── metric_catalog.py      # 指标检索
├── glossary.py            # 术语解析
├── ambiguity_catalog.py   # 歧义匹配/渲染/排序
├── example_catalog.py     # SQL 示例检索
├── context_builder.py     # 拼 prompt 上下文
├── provider.py            # 对 B 的 5 个方法
├── validate.py            # 数据一致性校验 CLI
└── inspect.py             # 演示 CLI
```

## 3. 对外接口

`FileKnowledgeProvider` 保留 5 个方法，返回 dataclass：

```python
provider.get_schema_context(question, intent=None) -> SchemaContext
provider.search_metrics(question, top_k=5) -> list[MetricInfo]
provider.resolve_terms(question) -> list[TermResolution]
provider.detect_ambiguities(question, terms=None, asked_codes=None) -> AmbiguityReport
provider.search_sql_examples(question, top_k=3) -> list[SQLExample]
```

`SchemaContext`：

```python
@dataclass
class SchemaContext:
    tables: list[str]
    columns: dict[str, list[str]]
    join_paths: list[dict]
    prompt_text: str
    char_count: int
```

`AmbiguityReport`：

```python
@dataclass
class AmbiguityReport:
    items: list[dict]       # 已渲染 {term}，含 code/priority/blocking/options/default
    has_blocking: bool
```

## 4. 各脚本职责

### `models.py`

- `ColumnInfo`：`name/type/description/nullable/aliases/time_role/enum_values/is_pii/pii_policy/dictionary`
- `TableInfo`：`name/description/grain/primary_key/columns/relationships`
- `MetricInfo`：`id/name/category/aliases/definition/formula/gold_sql_ref/related_tables/time_field/default_interpretation/ambiguity_notes/unit`
- `TermInfo`：`term/aliases/target_type/target/source/confidence/match/values`
- `AmbiguityRule`：`code/category/trigger_terms/question/options/default/blocking/priority/related_columns/related_metrics`
- `SQLExample`：`id/source_gold_id/question/sql/tables/pattern/notes`
- `KnowledgeBundle`：上述集合 + `needs_value_dictionary` + 版本
- `from_dict()` 统一忽略未知字段，避免再次 `TypeError`

### `loader.py`

- 加载：`schema.json`、`metrics.json`、`gold_sql.json`、`glossary.json`、`ambiguities.json`、`sql_examples_sqlite.json`
- 校验：
  - 版本一致（schema 1.1.0，其它 1.1.0）
  - `metrics.gold_sql_ref` 存在
  - `gold.metric_id` 存在
  - glossary target 的表/列/指标存在
  - ambiguity related_columns/metrics 存在
  - example tables 存在
- 返回 `KnowledgeBundle`
- 失败抛 `KnowledgeDataError`，带可定位信息

### `schema_catalog.py`

- `get_table(name)` / `get_column(table, column)`
- `find_tables(terms, metrics, examples)`：
  - 从 term target、metric.related_tables、example.tables 收集
  - 沿 relationships 扩一跳
  - 最多 6 张表
  - 没有命中时用 `visit/patient/medical_expense_rec` 兜底
- `join_paths(table_names)`：两端都在集合里的 relationship
- `to_prompt_context(table_names, focus_columns, max_chars=8000)`：
  - 只渲染相关表
  - 列优先级：主键/join 列 > 时间列 > 枚举列 > PII 列 > 术语/指标涉及的列 > 其他
  - 带枚举、时间语义、PII 提醒
  - 带 join 路径、扇出提醒
  - 截断到 max_chars

### `metric_catalog.py`

- `search(question, top_k=5)`：
  - 名称精确/别名精确/名称包含/别名包含/定义包含/分类包含
  - 返回完整 `MetricInfo`
- `get(metric_id)`
- `to_prompt_context(metrics)`：名称、别名、定义、公式、时间字段、单位、gold ref

### `glossary.py`

- `resolve(question)`：
  - 匹配 term/aliases
  - 支持 `match=enum/in/contains/time_range/metric`
  - 返回 `TermResolution`：命中词、target、confidence、source
  - 对“心内科”这类未收录但以“科”结尾的词，生成低置信度 `visit.dept_name contains` 推断，标 `source=heuristic.department`
  - 不编具体科室值

### `ambiguity_catalog.py`

- `detect(question, terms, asked_codes=None)`：
  - 命中 trigger_terms（最长匹配）
  - 渲染 `{term}`
  - 过滤 `asked_codes`
  - 只保留 `blocking`（或全部返回，provider 再筛）
  - 按 priority 排序
  - 返回 `AmbiguityReport`
- 现有 8 条规则全部支持

### `example_catalog.py`

- `search(question, metrics, terms, top_k=3)`：
  - 指标/别名命中 +50
  - 表重叠 +10/表
  - 问题 bigram 重叠 +0~30
  - pattern 关键词（排名/占比/平均/费用/人次/手术/住院/门诊）+5
  - 返回 top-k，不再单字匹配
- `to_prompt_context(examples)`：问题 + SQL（截断）

### `context_builder.py`

- `build(question, terms, metrics, examples, schema_catalog, max_chars=8000)`：
  - 选相关表
  - 拼：相关表 + join 路径 + 指标口径 + 相似 SQL + 约束
  - 控制在 8k 字符内
  - 返回 `SchemaContext`

### `provider.py`

- 初始化：接受 `data_dir` 或 `KnowledgeBundle`
- 5 个方法调用上面的 catalog
- 不再直接读 JSON
- `get_schema_context` 返回 `SchemaContext`

### `inspect.py`

- 去掉 emoji；`sys.stdout.reconfigure(encoding="utf-8")`
- 打印：
  - 术语解析
  - 指标候选
  - 歧义（已渲染）
  - 示例
  - 相关表 + join 路径
  - 上下文长度 + 前 1000 字符
- 不再打印 49k 全量 schema

### `validate.py`

- CLI：`python -m app.knowledge.validate --data-dir data`
- 检查数量、版本、引用、关系列、示例表
- 失败退出码 1

## 5. 实施顺序

1. `models.py`
2. `loader.py`
3. `schema_catalog.py`
4. `metric_catalog.py`
5. `glossary.py`
6. `ambiguity_catalog.py`
7. `example_catalog.py`
8. `context_builder.py`
9. `provider.py`
10. `inspect.py`
11. `validate.py`
12. 跑验收

## 6. 验收

- `python -m app.knowledge.validate --data-dir data` 通过；
- `load_knowledge("data")` 返回 bundle，不报错；
- provider 5 个方法返回稳定 dataclass；
- `get_schema_context("上个月心内科门诊医保患者的次均费用是多少")`：
  - 表只含 `visit/patient/medical_expense_rec` 等；
  - 有 join 路径；
  - 有 `activity_type_code` 枚举；
  - 有 PII 提醒；
  - ≤8k 字符；
- `search_metrics("次均费用")` 返回门诊/住院次均费用候选；
- `detect_ambiguities`：
  - `{term}` 已渲染；
  - 按 priority 排序；
  - 不重复 `asked_codes`；
- `search_sql_examples` 返回与问题相关的示例；
- `inspect` 在 Windows 默认终端能跑；
- 不 import `app.graph` / `app/api`。
