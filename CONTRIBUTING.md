# Contributing

## Setup

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
pre-commit install
```

## Standards

- Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, `perf:`, `ci:`
- Every module carries type hints; `mypy --strict` must pass
- `ruff check` and `ruff format` must pass
- Core logic keeps test coverage above 80%
- Configuration comes from environment variables only; never commit secrets

## Before opening a pull request

```bash
ruff check .
ruff format --check .
mypy src/
pytest
```
