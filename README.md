# ai-platform-mini

Minimal FastAPI scaffold for an AI platform backend.

## Project rules

- Python version: support `3.12` to `3.14`, with `3.14` as the default local version
- Style: follow PEP 8 and keep formatting/linting green with Ruff
- Type hints: add type hints early; all new or edited production code should be annotated
- Sprint rule: every Sprint must end with a runnable app and passing checks
- Git workflow: push to GitHub from day one with small, meaningful commits

## Quick start

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## Quality gate

```bash
ruff format --check .
ruff check .
mypy app tests
pytest
```

## Health check

After starting the app, open `http://127.0.0.1:8000/api/health`.
