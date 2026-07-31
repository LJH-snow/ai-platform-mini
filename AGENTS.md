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

## Git and GitHub

- Push to GitHub from day one to preserve a full development history.
- Make small, meaningful commits instead of large batch commits.
- Push at least once when the scaffold changes meaningfully and again at the end of each Sprint.
- Protect `main` on GitHub and require the `ci` status check before merging.
