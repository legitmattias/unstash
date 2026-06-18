"""FastAPI-Users wiring: session backend, user manager, dependencies, router."""

from __future__ import annotations

from unstash.auth.backend import auth_backend, fastapi_users
from unstash.auth.dependencies import current_active_user, current_superuser

__all__ = [
    "auth_backend",
    "current_active_user",
    "current_superuser",
    "fastapi_users",
]
