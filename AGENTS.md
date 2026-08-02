# Project Rules

## Runtime

- Support Python `3.12` through `3.14`.
- Use Python `3.14` as the default local version unless a task specifically requires testing on another supported version.
- Keep `.python-version` pointed at the preferred local default, keep `pyproject.toml` aligned with the supported version range, and keep CI validating supported versions.
- If the local virtual environment is not based on a supported Python version, recreate it before feature work continues.

## Style

- Follow PEP 8.
- Run `ruff format --check .` and `ruff check .` before each commit.
- Keep imports sorted and grouped consistently.

## Type Hints

- Add type hints early.
- Every new or edited production function, method, and class should include explicit type annotations.
- Prefer concrete types over `Any`. If `Any` is unavoidable, keep it at integration boundaries and minimize its spread.
- Run `mypy app tests` before each commit.

## Sprint Definition of Done

- At the end of every Sprint, the project must remain runnable.
- The following checks must pass before a Sprint is considered complete:
  - `ruff format --check .`
  - `ruff check .`
  - `mypy app tests`
  - `pytest`
- The application must still start with `uvicorn app.main:app --reload`.

## Code Review Flow (IMPORTANT)

After writing or modifying any code, do NOT proceed to the next feature automatically. Instead, follow this flow:

1. GLM writes/modifies code.
2. Run the project to verify it works (`ruff format --check .`, `ruff check .`, `mypy app tests`, `pytest`).
3. Present the changed code to the user for **Code Review** before moving on.
4. Wait for the user's feedback on:
   - Any patterns not recommended in enterprise projects.
   - Architecture issues.
   - Hidden bugs.
   - Optimization opportunities.
5. Apply fixes based on the user's review feedback, then repeat from step 2 if needed.
6. Only after the user approves, proceed to the next task.

This ensures the final project is not just runnable, but a Code-Reviewed, enterprise-grade codebase.

## Sprint Completion Checklist

At the end of every Sprint, complete these three things:

1. **Git Commit** — Stage and commit with a conventional commit message (e.g. `feat: integrate Ollama chat endpoint`).
2. **Update README** — Document what was added or changed in this Sprint.
3. **Learning Summary** — Write a brief summary (≤5 sentences): What was learned? Why this design? What problems arose? How were they solved?

## Deferred Optimizations

These improvements were identified during Code Review and will be implemented in future Sprints:

- **Global exception handlers** — Move `try/except` from Router to `app/core/exception_handlers.py` using `@app.exception_handler(...)` (Day 5)
- **Request ID middleware** — Add `request_id` to every log entry for production request tracing (Day 5)
- **Lifespan for init** — Move logging/DB/Redis initialization from `create_app()` to FastAPI `lifespan` context manager (Day 5+)
- **Middleware exception logging** — Wrap `call_next(request)` in try/except so 500 errors are also logged (Day 5+)
- **Secret fields** — Add `openai_api_key` etc. with `.env` only, never committed to Git (Sprint 5+)

## Git and GitHub

- Push to GitHub from day one to preserve a full development history.
- Make small, meaningful commits instead of large batch commits.
- Push at least once when the scaffold changes meaningfully and again at the end of each Sprint.
- Protect `main` on GitHub and require the `ci` status check before merging.
