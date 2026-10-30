# 组员 A 开发文档：知识层

知识层把医疗业务事实变成 Agent 可检索的结构化知识：数据库模式、表关系、35 个业务指标口径、
术语/同义词、歧义规则、SQLite few-shot 示例。图谱层（B）与 SQL 生成层（C）都只通过
`FileKnowledgeProvider` 的 5 个方法取知识。

**边界**：不 import `app.graph` / `app.api`；不连接任何数据库；不执行 SQL；不做评测。

## 1. 数据文件

```text
data/
├── schema.json                 # 13 张表、791 列、17 条关系；dialect=oracle（原始仓库）
├── metrics.json                # 35 个指标（门急诊 16 / 住院 12 / 手术 7）
├── gold_sql.json               # 35 条参考 = 29 条 SQL + 6 条公式；只做引用校验，不执行
├── glossary.json               # 85 条术语 + 4 条 needs_value_dictionary
├── ambiguities.json            # 8 条歧义规则
└── sql_examples_sqlite.json    # 15 条可在镜像上执行的 few-shot
```

六个文件 `version` 都是 `1.1.0`；`sql_examples_sqlite.json` 还带 `schema_version`。加载时会校验它们
与 `schema.json` 的版本一致。

`data/synthetic/medical.db` 不在这里——它是 C 的 `build_db` 产物，运行时生成、被 gitignore 忽略。

## 2. 模块

| 文件 | 职责 |
|---|---|
| `models.py` | 6 个 JSON 的 dataclass；忽略未知字段，schema 加列不会让加载 TypeError |
| `loader.py` | 读 6 个文件 → `KnowledgeBundle`；版本、引用、关系列、示例表全部校验 |
| `schema_catalog.py` | 选相关表、join 路径、按预算裁剪列、渲染 schema 文本 |
| `metric_catalog.py` | 按名称/别名/定义检索指标，处理"住院"与"门急诊"范围冲突 |
| `glossary.py` | 术语解析；`needs_value_dictionary` 之外的科室名按模式低置信度推断 |
| `ambiguity_catalog.py` | 命中规则、渲染 `{term}`、过滤已问 code、按 priority 排序 |
| `example_catalog.py` | 按指标/表/文本 bigram 重叠/pattern 检索 SQL 示例 |
| `context_builder.py` | 把 schema + 术语 + 指标 + 示例拼成一段 prompt，控制在 8000 字符 |
| `provider.py` | 对外的唯一入口（5 个方法） |

## 3. Provider 接口

```python
from app.knowledge.loader import load_knowledge
from app.knowledge.provider import FileKnowledgeProvider

bundle = load_knowledge("data")          # 也可 FileKnowledgeProvider("data") 内部加载
provider = FileKnowledgeProvider(bundle)

provider.get_schema_context(question, max_chars=8000)  # SchemaContext
provider.search_metrics(question, top_k=5)             # list[MetricInfo]
provider.resolve_terms(question)                       # list[TermResolution]
provider.detect_ambiguities(question, terms=terms, asked_codes=None)  # AmbiguityReport
provider.search_sql_examples(question, top_k=3)        # list[SQLExample]
```

返回对象定义在 `models.py`：

| 对象 | 关键字段 |
|---|---|
| `SchemaContext` | `tables`、`columns`（每表最多 25 列）、`join_paths`、`prompt_text`、`char_count` |
| `MetricInfo` | `id/name/category/aliases/definition/formula/gold_sql_ref/related_tables/time_field/unit` |
| `TermResolution` | `term/canonical_term/target_type/target/confidence/source/match/values` |
| `AmbiguityReport` | `items`（code、question、options、default、blocking、priority…）、`has_blocking` |
| `SQLExample` | `id/question/sql/tables/pattern/notes` |

`get_schema_context` 内部依次调用术语解析、指标检索、示例检索，再交给 `build_context`；
图里的 `analyze` 和 `finalize` 节点还会分别直接调用 `resolve_terms` / `search_metrics` /
`detect_ambiguities`，所以指标检索要足够快（纯字符串评分，无模型调用）。

## 4. 检索规则（当前实现）

### 4.1 指标检索 `metric_catalog.py`

- 名称命中 100 分、别名 90 分；取问题中最长的命中片段。
- "嵌套命中"降权：`急诊人次` 落在更长的 `门急诊人次` 里时扣 40，避免短词抢长词。
- 没有字面命中时用 bigram 覆盖率做模糊匹配（≥0.6 才生效，权重打 6 折），例如
  「住院收入」能匹配到「住院总收入」。
- 定义/分类字面命中 +10，"相关表出现在问题里" +5。
- **范围词强约束**：问题里有「住院」时，门急诊指标扣 60；有「门诊/急诊/门急诊」时，住院指标扣 60。
  这条规则修的是「2025年1月住院总收入」被解析成门诊收入的问题。
- 有名称/别名命中（≥50 分）时，不再混入只有定义命中的弱候选。

### 4.2 术语解析 `glossary.py`

- 术语或别名是问题的子串即命中（单字只允许「男/女」）；同一 target 去重，按长度与置信度排序。
- 科室名不在 schema 字典里：问题中出现 `X科` 且没有已解析的 `visit.dept_name` 术语时，
  以 `confidence="low"`、`source="heuristic.department"` 推断为 `visit.dept_name contains X科`。
  这是模式匹配，不是编造字典值；镜像里只有内科/外科/儿科/妇产科，所以「心内科」会正确地查不到数据。

### 4.3 歧义规则 `ambiguity_catalog.py`

- 规则命中 = 问题包含任一 `trigger_terms`（取最长命中作为 `matched_term`），
  `{term}` 占位符被替换后作为问题文本。
- `asked_codes` 里的 code 不再返回（每个问题只问一次）。
- 按 `priority`、`code` 排序。是否真的提问由 B 的 `ambiguity_policy` 决定：
  非 blocking 项不提问；`metric_scope` 与明确年月由 `analyze` 直接标记为已解决。

### 4.4 SQL 示例检索 `example_catalog.py`

指标名出现在示例问题里 +50；共享表每个 +10；问题与示例问题的 bigram 重叠最多 +30；
业务关键词每命中 +5。返回 top-k（默认 3）。

## 5. 上下文构建 `context_builder.py`

按顺序拼：schema（最多 `max_chars * 0.55`）→ 约束 → 术语 → 指标口径 → 相似示例，
最终不超过 `max_chars`（默认 8000，超出截断并标注）。

- 选表：术语 target 的表 → 指标 `related_tables` →（都没有时才用示例表）→ 兜底
  `visit / patient / medical_expense_rec`，最多 6 张。
- 列预算每表 25 列，优先级：主键/join 列/focus 列 → 时间列 → 枚举列 → 金额列 → 普通列 → PII 列。
- 关系只渲染两端都被选中的 join 路径，带 `fanout` 与 `filter` 说明。
- **方言提示只写可执行方言**：`schema.json` 声明 `oracle`，但本轮只在 SQLite 上执行，
  所以 prompt 里写「可执行方言 sqlite；原始方言 oracle 仅作参考」，并禁止 `NVL/TO_CHAR/ROWNUM`。
  否则模型会生成 SQLite 解析不了的 Oracle 写法。

## 6. 校验与自检

`loader.load_knowledge()` 在加载时做完整校验，任何问题都抛 `KnowledgeDataError`：

- 六个文件存在、是合法 JSON；
- `version` 全部等于 `schema.json` 的 version；
- schema：主键列存在；关系目标表存在；`on` 条件里引用的 `table.column` 存在；
- metrics：`gold_sql_ref` 指向存在的 gold 条目；`related_tables` 存在；`time_field` 的列存在；
- gold：`metric_id` 存在、表存在；
- glossary：metric/table/column target 存在；
- ambiguities：`related_columns` 与 `related_metrics` 存在；
- examples：表名存在。

`/health` 的 `knowledge` 检查就是调用它；失败时 `detail` 会带出完整错误列表。
本地自检：

```powershell
.venv\Scripts\python.exe -c "from app.knowledge.loader import load_knowledge; b=load_knowledge('data'); print('ok', len(b.tables), len(b.metrics), len(b.gold_items), len(b.terms), len(b.ambiguities), len(b.examples))"
# ok 13 35 35 85 8 15
```

看某个问题的检索结果（不含 LLM）：

```powershell
.venv\Scripts\python.exe -c "from app.knowledge.provider import FileKnowledgeProvider; p=FileKnowledgeProvider('data'); q='上个月门诊人次是多少'; print([m.id for m in p.search_metrics(q)]); print([t.term for t in p.resolve_terms(q)]); print([i['code'] for i in p.detect_ambiguities(q).items]); print(p.get_schema_context(q).char_count)"
```

## 7. 扩展方式

### 新增指标

1. 在 `data/metrics.json` 的 `metrics` 加一条，字段：`id/name/category/aliases/definition/formula/
   gold_sql_ref/related_tables/time_field/default_interpretation/unit`。
2. 在 `data/gold_sql.json` 加对应 SQL 或公式条目（`kind` 为 `sql` 或 `formula`）。
3. 必要时在 `data/glossary.json` 加别名术语。
4. 把六个文件的 `version` 一起升到新版本号（加载器要求全部一致），重跑第 6 节自检。

### 新增术语

```json
{
  "term": "门诊",
  "aliases": ["门诊患者"],
  "target_type": "enum",
  "match": "enum",
  "target": { "table": "visit", "column": "activity_type_code", "value": "1" },
  "source": "schema.enum",
  "confidence": "high"
}
```

`target_type` 支持 `enum / in / contains / time_range / metric / table / column`。

### 新增歧义规则

在 `data/ambiguities.json` 加 `code/category/trigger_terms/question/options/default/blocking/priority/
related_columns/related_metrics`。`question` 可用 `{term}` 引用命中词。新增 blocking 规则后，
用 [TEST_QUESTIONS.md](TEST_QUESTIONS.md) 的用例确认不会把「明确年月」的问题也拖进澄清
（明确年月由 `app/graph/nodes/analyze.py` 的正则消解 `time_range`）。

### 新增 SQL 示例

在 `data/sql_examples_sqlite.json` 加 `id/source_gold_id/question/sql/tables/pattern/notes`，
单条建议 ≤20 行，且必须能在镜像上执行——`build_db` 末尾会把全部示例跑一遍，失败返回非 0。

## 8. `needs_value_dictionary`

`glossary.json` 里的 `needs_value_dictionary` 是**待补真实值的清单**（科室具体名称、诊断名称、
检查项目名称、医保类型细分标签）。这些值不在 schema 里，不能凭空编。拿到真实值或数据字典后：

1. 在 `terms` 里加具体术语；
2. 从 `needs_value_dictionary` 删除对应条目；
3. 重跑第 6 节自检。

## 9. 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\knowledge -q
```

覆盖：指标检索（长短词嵌套、住院/门急诊范围、模糊匹配）、schema 上下文（选表、列预算、
join 路径）、上下文构建（预算截断、方言说明）。
