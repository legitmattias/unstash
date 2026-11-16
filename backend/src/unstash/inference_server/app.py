"""HTTP application of the inference service.

Run with::

    uvicorn --factory unstash.inference_server.app:create_app

The models are loaded before the server accepts requests, so a passing
health check means both are ready.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, FastAPI, Request

# Engine is resolved at runtime by FastAPI's dependency annotations.
from unstash.inference_server.engine import Engine  # noqa: TC001
from unstash.inference_server.schemas import (
    EmbedRequest,
    EmbedResponse,
    RerankRequest,
    RerankResponse,
)
from unstash.inference_server.settings import InferenceServerSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


def _load_engine() -> Engine:
    # Imported here so importing this module does not import torch.
    from unstash.inference_server.engine import SentenceTransformerEngine  # noqa: PLC0415

    return SentenceTransformerEngine(InferenceServerSettings())


def _engine(request: Request) -> Engine:
    return request.app.state.engine


# Handlers are synchronous so FastAPI runs them in its thread pool and the
# event loop keeps answering health checks during inference.
router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Report readiness; the server only listens once the models are loaded."""
    return {"status": "ok"}


@router.post("/embed")
def embed(body: EmbedRequest, engine: Annotated[Engine, Depends(_engine)]) -> EmbedResponse:
    """Embed texts as passages or queries."""
    vectors, total_tokens = engine.embed(body.texts, is_query=body.task == "query")
    return EmbedResponse(vectors=vectors, total_tokens=total_tokens)


@router.post("/rerank")
def rerank(body: RerankRequest, engine: Annotated[Engine, Depends(_engine)]) -> RerankResponse:
    """Score documents against a query."""
    return RerankResponse(scores=engine.rerank(body.query, body.documents))


def create_app(engine_loader: Callable[[], Engine] = _load_engine) -> FastAPI:
    """Build the application; ``engine_loader`` runs once at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine_loader()
        yield

    app = FastAPI(title="Unstash inference", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(router)
    return app
