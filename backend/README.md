# Backend

FastAPI backend for Unstash.

## Stack

- FastAPI + Python 3.12+
- SQLAlchemy 2.0 async with asyncpg
- Pydantic for validation and settings
- FastAPI-Users for authentication
- Taskiq for async job queue
- structlog for structured logging
- uv for package management
- Ruff for linting and formatting
- pyright for type checking
- pytest + pytest-asyncio for testing

## Layout

```
src/unstash/    Application code (src layout)
tests/          Test suite
pyproject.toml  Project configuration and dependencies
```

## Getting started

See the top-level [README](../README.md) for development setup using Docker Compose.
