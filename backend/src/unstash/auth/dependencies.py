"""Shared FastAPI dependencies for authenticated routes."""

from __future__ import annotations

from unstash.auth.backend import fastapi_users

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
