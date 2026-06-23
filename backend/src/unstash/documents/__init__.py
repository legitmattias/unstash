"""Document ingestion routes and helpers.

The HTTP surface (upload, list, single, job-progress) lives in
:mod:`unstash.documents.router`. The on-disk storage helper and the
worker task that drives status transitions live alongside.
"""

from __future__ import annotations

from unstash.documents.router import documents_router

__all__ = ["documents_router"]
