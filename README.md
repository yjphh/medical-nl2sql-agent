# Medical NL2SQL Agent

将自然语言问题转换为 SQL 并返回可解释结果的智能体，包含意图识别、歧义检测、澄清对话、SQL 生成与校验、答案合并等环节。

## 快速开始

```bash
pip install -e ".[dev]"
cp .env.example .env  # 填写 LLM 与数据库配置
uvicorn app.main:app --reload
```

## 目录

- `app/` — 核心后端（API、LangGraph 工作流、知识库、工具、记忆）
- `docs/` — 架构与开发文档
- `data/` — schema / 指标口径知识文件与合成数据
- `tests/` — 单元 / 集成测试
- `frontend/` — 前端（待实现）
