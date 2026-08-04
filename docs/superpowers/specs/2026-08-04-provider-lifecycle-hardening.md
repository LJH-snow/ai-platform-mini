# Sprint 7.4: Provider Lifecycle Hardening

## 1. 背景

Sprint 7.2 引入 `ProviderRouter`，其 `close()` 方法在关闭多个 Provider 时收集
异常。当 `CancelledError` 与普通 `Exception` 并存时，抛出 `BaseExceptionGroup`，
而 FastAPI lifespan 的 `except Exception` 无法捕获，导致未处理异常可能穿透
lifespan、中断优雅关闭。

Sprint 8 将新增 `OllamaEmbedder`（持有共享 HTTP 客户端，需要关闭），继续在
资源关闭语义不稳定的情况下增加可关闭组件会放大生命周期问题。

## 2. 目标

1. `ProviderRouter.close()` 将 `CancelledError` 包装为 `RuntimeError`，确保
   只抛出 `Exception` 子类（`RuntimeError` 或 `ExceptionGroup`）。
2. lifespan 的 `except Exception` 可以正常捕获 Router 抛出的所有关闭异常。
3. `CancelledError` 仍然被记录，不会在静默中丢失。
4. 外部取消信号（Uvicorn/编排平台对 lifespan 本身的取消）继续正常传播，
   不被 `close()` 的内部异常处理吞掉。
5. 保持所有 Provider 都被尝试关闭，即使部分已失败。

## 3. 非目标

- 不修改 `LLMProvider` Protocol。
- 不改变 ProviderRouter 的路由逻辑。
- 不修改 `OpenAIProvider` 或 `OllamaProvider`。
- 不处理 naive `created_at` UTC 规范化或空流 fallback 模型字段。
- 不引入 DI 容器或 lifespan 重构。

## 4. 设计

### 4.1 ProviderRouter.close()

**当前行为**：

```python
except asyncio.CancelledError as exc:
    errors.append(exc)        # BaseException 子类混入 list[BaseException]
except Exception as exc:
    errors.append(exc)
# ...
raise BaseExceptionGroup(...)  # CancelledError 在里面 → 真正的 BaseExceptionGroup
```

**修复方案**：区分外部取消和 Provider 内部取消。

当 `close()` 所在任务被外部取消（`current_task().cancelling() > 0`）时，
保存原始 `CancelledError`，继续尝试关闭剩余 Provider，完成后重新抛出取消信号。
当 Provider 自身在非外部取消场景下抛出 `CancelledError` 时，包装为
`RuntimeError`，使其成为 `Exception` 子类。

```python
except asyncio.CancelledError as exc:
    current = asyncio.current_task()
    if current is not None and current.cancelling() > 0:
        external_cancel = exc
    else:
        errors.append(RuntimeError(f"Provider close cancelled: {exc}"))
except Exception as exc:
    errors.append(exc)

# After loop:
if external_cancel is not None:
    if errors:
        logger.error("Provider close errors suppressed by external cancellation: %s", errors)
    raise external_cancel
```

效果：

| 场景 | 当前 | 修复后 |
|------|------|--------|
| 单个 Exception | RuntimeError | RuntimeError（不变） |
| 单个 Provider 内部 CancelledError | CancelledError（BaseException） | RuntimeError |
| 外部 task.cancel() | CancelledError 被吞掉 | CancelledError 继续传播 |
| 多个 Exception | ExceptionGroup | ExceptionGroup（不变） |
| Cancelled + Exception | BaseExceptionGroup | CancelledError 优先传播，Exception 记入日志 |
| 多个 Provider 内部 Cancelled | BaseExceptionGroup | ExceptionGroup |

所有输出都是 `Exception` 子类，lifespan 的 `except Exception` 可以捕获。

`errors` 的类型声明从 `list[BaseException]` 改为 `list[Exception]`。

### 4.2 单异常透传

当只有一个错误时，继续透传该异常本身（不包装为单元素 `ExceptionGroup`）。
Provider 内部抛出的 `CancelledError` 包装为 `RuntimeError` 后也遵循此规则。

### 4.3 外部取消优先传播

外部取消（`current_task().cancelling() > 0`）时，即使有其他关闭异常，
也优先重新抛出原始 `CancelledError`。其他异常通过 `logger.error` 记录，
确保不静默丢失。

### 4.4 Lifespan

lifespan 的 `except Exception` 无需修改——Router 修复后，Provider 内部取消
产生的关闭异常都是 `Exception` 子类。外部取消抛出的是 `CancelledError`，
不会被 `except Exception` 捕获，会正常传播到 Uvicorn/编排平台。

## 5. 测试策略

### 5.1 更新现有测试

`test_close_preserves_cancellation_and_provider_failure`：
- 改为断言 `ExceptionGroup`（不再是 `BaseExceptionGroup`）。
- 验证内部异常包含一个 `RuntimeError`（包装自 Provider 内部 CancelledError）和一个
  `RuntimeError`（原始 Provider 关闭失败）。

`test_close_attempts_all_providers_before_reraising_cancellation`：
- 改为断言 `RuntimeError`（不再是 `CancelledError`）。
- 验证消息包含 "cancelled"。

### 5.2 新增测试

- **纯 CancelledError 多 Provider**：两个 Provider 都因内部取消失败，断言
  `ExceptionGroup` 包含两个 `RuntimeError`。
- **外部取消传播**：第一个 Provider 在 `close()` 中等待，从外部调用
  `task.cancel()`，断言最终抛出 `asyncio.CancelledError`，任务 `cancelled()`
  为真，两个 Provider 都被尝试关闭。
- **lifespan 捕获 ExceptionGroup 回归**：模拟 Router 抛出
  `ExceptionGroup`，验证 lifespan `except Exception` 可以捕获并记录。
- **重复关闭**：对同一 Router 调用 `close()` 两次，验证不崩溃。

### 5.3 保留的现有测试

以下测试不需要修改：

- 正常关闭、共享 Provider 单次关闭、单个 Exception 透传、多个 Exception
  的 ExceptionGroup、Protocol 满足性。

## 6. 文件变更范围

- 修改 `app/providers/router.py`
- 修改 `tests/test_provider_router.py`
- 修改 `tests/test_lifespan.py`（新增 lifespan 捕获 ExceptionGroup 测试）
- Sprint 收尾时更新 `README.md` 和 `AGENTS.md`

## 7. 完成标准

1. `ProviderRouter.close()` 对 Provider 内部取消只抛出 `Exception` 子类。
2. 外部取消（`task.cancel()`）传播原始 `CancelledError`，不被吞掉。
3. 所有 Provider 始终被尝试关闭。
4. CancelledError 信息通过 `RuntimeError` 保留，不静默丢失。
5. lifespan `except Exception` 可以捕获 Router 的非取消关闭异常。
6. `ruff format --check .`、`ruff check .`、`mypy app tests` 和 `pytest`
   全部通过。
7. `git diff --check` 通过。
8. 完成 Code Review 后再提交 Sprint 实现并推送。
