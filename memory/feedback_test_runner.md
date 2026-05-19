---
name: feedback_test_runner
description: Use uv run pytest to run tests in this project
metadata:
  type: feedback
---

Always run tests with `uv run pytest`, not `python -m pytest` or bare `pytest`.

**Why:** The project uses uv for dependency management; pytest is only available through uv's venv.

**How to apply:** Any time tests need to be run in this repo, use `uv run pytest [args]`.
