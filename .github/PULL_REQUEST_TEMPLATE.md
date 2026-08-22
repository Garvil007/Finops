## What

<!-- One paragraph: what this change does. -->

## Why

<!-- The problem, or the behaviour that was wrong. Link the issue if there is one. -->

## How it was tested

<!-- Be specific. "Ran the tests" is not a test plan; name what you exercised. -->

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy src/`
- [ ] `pytest` (coverage gate at 80% passes)
- [ ] Postgres suite, if this touches SQL: `pytest tests/integration/test_postgres_compat.py`
- [ ] Manual verification:

## Notes for the reviewer

<!-- Migrations, config changes, anything deliberately left out, follow-up work. -->
