"""Pydantic schemas for the document ingestion routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    """A single document record returned to clients."""

    id: uuid.UUID
    org_id: uuid.UUID
    title: str
    mime_type: str
    size_bytes: int
    content_hash: str
    status: str
    parsing_error: str | None
    language: str | None
    pipeline_version: str | None
    pipeline_config: dict[str, Any] | None
    indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobProgressRead(BaseModel):
    """A background job's progress as visible to the API."""

    id: uuid.UUID
    org_id: uuid.UUID
    task_id: str
    task_name: str
    status: str
    progress_percent: int | None
    progress_detail: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentUploadResponse(BaseModel):
    """Response body for ``POST /api/orgs/{slug}/documents``."""

    document_id: uuid.UUID
    job_id: uuid.UUID
