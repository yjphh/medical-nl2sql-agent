# 项目目录结构

```
medical-nl2sql-agent/
├── README.md                 # 项目介绍与快速开始
├── pyproject.toml            # 依赖与打包配置
├── .env.example              # 环境变量模板（LLM、数据库等）
├── .gitignore
├── import_schema.py          # 从数据库导出 schema 到 data/schema.json
│
├── docs/                     # 项目文档
│   ├── DEVELOPMENT.md        # 开发环境与常用命令
│   ├── ARCHITECTURE.md       # 整体架构与工作流图
│   ├── API.md                # HTTP 接口说明
│   ├── DECISIONS.md          # 架构决策记录 (ADR)
│   └── STRUCTURE.md          # 本文件：目录结构说明
│
├── app/                      # 后端核心代码
│   ├── main.py               # FastAPI 应用入口
│   ├── config.py             # 配置加载（pydantic-settings）
│   ├── api/                  # HTTP 层
│   │   ├── routes_sessions.py  # 会话创建/查询
│   │   ├── routes_queries.py   # 提交自然语言查询
│   │   └── schemas.py          # 请求/响应模型
│   ├── graph/                # LangGraph 工作流
│   │   ├── builder.py         # 组装并编译状态图
│   │   ├── state.py           # GraphState 状态定义
│   │   ├── routing.py         # 节点间条件路由
│   │   └── nodes/             # 工作流节点
│   │       ├── intent_schema.py    # 意图识别 + schema 上下文
│   │       ├── ambiguity.py        # 歧义检测
│   │       ├── clarification.py    # 生成澄清问题
│   │       ├── sql_generation.py   # SQL 生成
│   │       ├── validation.py       # SQL 只读/语法校验
│   │       └── answer_merger.py    # 结果归纳为自然语言
│   ├── prompts/              # LLM 提示词模板（预留）
│   ├── knowledge/            # 领域知识（读取 data/ 下知识文件）
│   │   ├── models.py           # 6 个 JSON 的 dataclass
│   │   ├── loader.py           # 统一加载 + 版本/引用校验
│   │   ├── schema_catalog.py   # 表/列/关系/相关表/上下文
│   │   ├── metric_catalog.py   # 指标检索
│   │   ├── glossary.py         # 术语解析
│   │   ├── ambiguity_catalog.py# 歧义规则匹配/渲染
│   │   ├── example_catalog.py  # SQL 示例检索
│   │   ├── context_builder.py  # 拼 prompt 上下文
│   │   ├── provider.py         # 对编排层的 5 个方法
│   │   ├── validate.py         # 数据校验 CLI
│   │   └── inspect.py          # 演示 CLI
│   ├── tools/                # 工具层
│   │   ├── sql_generator/      # SQL 生成器
│   │   │   ├── base.py           # 抽象接口
│   │   │   └── reforce_adapter.py # 外部引擎适配
│   │   └── sql_validator.py    # 只读校验、危险语句拦截
│   └── memory/               # 持久化与会话
│       ├── checkpointer.py     # LangGraph checkpointer 封装
│       └── session_repository.py # 会话与多轮历史
│
├── frontend/                 # 前端（预留，见其 README）
├── data/                     # 领域知识与数据
│   ├── schema.json           # 数据库表/列元数据（knowledge 加载）
│   ├── metrics.json          # 业务指标口径定义（knowledge 加载）
│   └── synthetic/            # 合成演示数据（预留）
└── tests/                    # 测试
    ├── unit/                 # 单元测试（sql_validator 等）
    └── integration/          # API 集成测试
```

## 知识文件加载关系

```
data/schema.json  ─┐
                   ├─▶ app/knowledge/loader.py ─▶ SchemaCatalog / MetricCatalog
data/metrics.json ─┘                                   │
                                                       ▼
                              intent_schema / sql_generation 节点拼入 prompt
```

## 读代码建议路径

1. `app/graph/builder.py` — 看清整体工作流
2. `app/graph/state.py` + `routing.py` — 状态与分支逻辑
3. `app/api/routes_queries.py` — 对外入口如何触发工作流
4. `app/knowledge/` + `data/*.json` — schema 与指标口径如何进 prompt
5. `app/tools/sql_validator.py` — 安全校验规则
