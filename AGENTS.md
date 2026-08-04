# 项目规则

## 语言要求

- **所有回复必须使用中文**，包括解释、总结、学习笔记等。
- 代码注释保持英文（遵循行业惯例），但与用户的交流一律用中文。

## 运行时

- 支持 Python `3.12` 至 `3.14`。
- 默认使用 Python `3.14`，除非特定任务需要测试其他版本。
- 保持 `.python-version` 指向默认版本，`pyproject.toml` 版本范围对齐，CI 验证所有支持版本。
- 如果本地虚拟环境不是支持版本，先重建再继续开发。

## 代码风格

- 遵循 PEP 8。
- 每次提交前运行 `ruff format --check .` 和 `ruff check .`。
- imports 保持排序和分组一致。

## 类型注解

- 尽早添加类型注解。
- 所有新增或修改的生产函数、方法、类必须包含显式类型标注。
- 优先使用具体类型而非 `Any`。如不可避免，仅在集成边界使用，并最小化其传播。
- 每次提交前运行 `mypy app tests`。

## Sprint 完成定义

- 每个 Sprint 结束时，项目必须保持可运行状态。
- 以下检查必须全部通过才算 Sprint 完成：
  - `ruff format --check .`
  - `ruff check .`
  - `mypy app tests`
  - `pytest`
- 应用必须能通过 `uvicorn app.main:app --reload` 启动。

## Code Review 流程（重要）

写完或修改代码后，**不要**自动进入下一个功能，而是遵循以下流程：

1. GLM 编写/修改代码。
2. 运行验证（`ruff format --check .`、`ruff check .`、`mypy app tests`、`pytest`）。
3. 将变更的代码展示给用户进行 **Code Review**。
4. 等待用户反馈：
   - 企业项目中不推荐的模式。
   - 架构问题。
   - 隐藏 Bug。
   - 优化机会。
5. 根据用户反馈修复问题，如需要则从步骤 2 重新开始。
6. **只有用户批准后**，才进入下一个任务。

确保最终项目不仅是可运行的，而且是经过 Code Review 的企业级代码库。

## Sprint 完成清单

每个 Sprint 结束时，必须完成以下三件事：

1. **Git 提交** — 使用 conventional commit 消息暂存并提交（如 `feat: 集成 Ollama chat 端点`）。
2. **更新 README** — 记录本 Sprint 新增或变更的内容。
3. **学习总结** — 写一段简要总结（≤5 句话）：学到了什么？为什么这样设计？遇到了什么问题？如何解决的？

## 延迟优化

以下改进在 Code Review 中识别，将在未来 Sprint 中实现：

- **请求 ID 日志** — 在 RequestLoggingMiddleware 日志输出中加入 `request_id`（Sprint 2，已完成）
- **Lifespan 初始化** — 将 logging/DB/Redis 初始化从 `create_app()` 迁移到 FastAPI `lifespan` 上下文管理器（Sprint 3+）
- **dictConfig 日志** — 将 `basicConfig()` 升级为 `logging.config.dictConfig()` 支持 JSON/轮转/文件 handler（Sprint 3+）
- **密钥字段** — `openai_api_key` 等仅通过 `.env` 加载，绝不提交到 Git（Sprint 5+）
- **共享 HTTP 客户端** — 使用单个共享 `httpx.AsyncClient` 连接池，而非每次请求创建新实例（已完成）
- **Provider 层** — 从 Service 中提取 HTTP 逻辑到 `providers/`（已完成）
- **统一 _get_json/_post_json** — 重构为单一 `_request(method, path, ...)` 方法（已完成）
- **Adapter 层** — 从 OpenAIService 提取协议转换到 `adapters/`（Sprint 7.3，已完成）
- **Usage token 追踪** — 从 Ollama `prompt_eval_count`/`eval_count` 填充 `prompt_tokens`/`completion_tokens`（已完成）
- **Streaming usage 记录** — 通过 UsageCollector 在 Service 层记录流式请求的 token 用量（已完成）
- **naive created_at UTC 规范化** — 将无时区 ISO 时间戳强制解释为 UTC，需独立评估非 UTC 部署兼容性（Sprint 8+）
- **空流 fallback 模型字段** — `chat_completions_stream()` 空流 fallback 应使用已解析的 `model` 而非 `default_model`（Sprint 8+）
- **ProviderRouter.close() BaseExceptionGroup 泄漏** — `CancelledError` 与普通异常并存时产生 `BaseExceptionGroup`，lifespan `except Exception` 无法捕获（Sprint 7.4，已完成）

## Git 和 GitHub

- 从第一天起推送到 GitHub，保留完整开发历史。
- 做小而有意义的提交，而不是大批量提交。
- 当脚手架发生实质性变化时至少推送一次，每个 Sprint 结束时再推送一次。
- 保护 `main` 分支，要求 `ci` 状态检查通过后才能合并。
