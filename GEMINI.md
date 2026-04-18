# GitHub REST API Project

A simple Python wrapper for GitHub REST APIs, optimized for use in GitHub Actions automation.

## Project Overview

- **Purpose:** Provide a streamlined interface for interacting with GitHub's REST API
  and performing Git operations within automation scripts.
- **Main Technologies:**
  - **Python 3.11+**: Core language.
  - **requests**: For HTTP interactions with the GitHub API.
  - **dulwich**: A pure-Python implementation of Git for repository operations.
  - **psutil**: For system and process utilities.
- **Architecture:**
  - `github_rest_api/github.py`: Contains the `GitHub` class for handling API requests (GET, POST, DELETE, PUT, PATCH).
  - `github_rest_api/actions/`: Focused utilities for GitHub Actions, including branch management and pushing changes.
  - `github_rest_api/actions/cargo/`: Specific support for Rust projects (benchmarking and profiling).
  - `github_rest_api/utils.py`: General-purpose utilities (versioning, partitioning).

## Building and Running

This project uses `uv` for dependency and environment management.

- **Setup Environment:**
  ```bash
  uv sync --all-extras
  ```
- **Code Formatting:**
  ```bash
  uv run ruff format ./
  ```
- **Linting:**
  ```bash
  uv run ruff check github_rest_api/ tests/
  ```
- **Type Checking:**
  ```bash
  uv run ty check
  ```
- **Dependency Analysis:**
  ```bash
  uv run deptry .
  ```
- **Running Tests:**
  ```bash
  uv run pytest
  ```

## Development Conventions

- **Code Style:** Strictly follows `ruff` formatting and linting rules.
- **Type Safety:** Uses `ty` (in addition to standard type hints) to ensure type correctness.
- **CI/CD:** Automated linting and formatting checks are performed
  on `push` to `dev`/`main` branches and on `pull_request` to `dev`.
- **Git Operations:** Prefers `dulwich` for programmatic Git interactions
  to avoid dependency on a system Git installation where possible.
