# ADR 0001: Initial Stack Choices

## Status

Accepted

## Context

Unstash is starting from a blank repository. We need to commit to a technology stack across many layers: frontend framework, backend framework, database, search infrastructure, document parsing, ML tooling, deployment, and operational tooling.

The project has specific requirements that narrow the space:

- **Adaptive document categorization** — the system must discover document types per organization without predefined taxonomies, requiring classical machine learning (clustering, classifiers) alongside modern embedding-based search.
- **Hybrid retrieval** — pure vector search is insufficient for Swedish documents (compound words, short queries, exact-term matches). We need both semantic (vector) and keyword (BM25) retrieval combined.
- **EU hosting / GDPR** — data must stay in the EU. This eliminates US-hosted API services for anything processing customer data unless they offer explicit EU instances.
- **Classical-first, LLM-surgical** — the architectural core of the project. We default to classical algorithms (clustering, classification, BM25, regex, NER) and reserve LLMs for tasks where they're uniquely valuable (cluster labeling, answer synthesis).
- **Operational simplicity** — the stack must be manageable without excessive operational complexity, but simplicity must never compromise quality or security.
- **Swedish priority, not locked** — Swedish is the primary language for quality tuning, but the product supports English from day one. Future Nordic language expansion is planned.
- **Professional standards from day one** — no shortcuts on security, testing, observability, or data integrity.

Extensive research into the competitive landscape, technical alternatives, and tradeoffs was conducted before this ADR was written.

## Decision

We adopt the following stack:

### Frontend
- **SvelteKit** with TypeScript for the full frontend (SSR + SPA where needed)
- **Open Props** for CSS custom properties and design tokens
- **Svelte scoped styles** for component-level CSS
- **Bits UI** for headless, accessible component primitives (dropdowns, dialogs, comboboxes)
- **Iconify** for icons on demand
- **pnpm** for package management

### Backend
- **FastAPI** on Python 3.12+ as the API framework
- **Uvicorn** as the ASGI server
- **FastAPI-Users** for authentication, sessions, and user management
- **Argon2id** for password hashing
- **SQLAlchemy 2.0 async** with **asyncpg** as the database driver
- **Pydantic** for input validation and settings management
- **structlog** for structured logging
- **uv** for Python package management
- **Ruff** for linting and formatting
- **pyright** for type checking

### Database and Search
- **PostgreSQL 17** as the primary database
- **pgvector** + **pgvectorscale** (Timescale) for vector similarity search with best-in-class performance at scale
- **ParadeDB pg_search** for real BM25 keyword search (not tsvector's approximation)
- **Reciprocal Rank Fusion** in SQL to combine vector and keyword results
- **Alembic** for migrations
- **pgBackRest** for backups and point-in-time recovery

### Document Ingestion
- **Docling** (IBM) for structure-aware text extraction (PDF, DOCX, XLSX, PPTX, HTML)
- **LibreOffice headless** for legacy format conversion (DOC, XLS, PPT, RTF, ODT)
- **EasyOCR** via Docling for scanned PDFs and images (better Swedish quality than Tesseract)
- **python-magic** (libmagic) for content-based MIME type detection

### Embeddings and Machine Learning
- **Jina AI v4** (EU-hosted in Germany, multilingual, open weights) for embeddings
- **BERTopic** (UMAP + HDBSCAN + c-TF-IDF) for adaptive document type discovery
- **scikit-learn** for classical clustering, classification, and TF-IDF
- **Calibrated Logistic Regression** on Jina embeddings + TF-IDF features for ongoing document classification (with proper probability scores for confidence-based LLM fallback)
- **spaCy** as NLP pipeline orchestrator
- **KB-BERT NER** (from KBLab / Kungliga Biblioteket) for Swedish named entity recognition
- **GLiNER** for zero-shot custom entity extraction
- **BGE-reranker-v2-m3** for multilingual cross-encoder reranking

### LLM (surgical usage)
- **Mistral Large 2** via Mistral AI API (Paris, EU-hosted) for cluster labeling and rare classification fallbacks
- **Local Ollama** with Mistral 7B as a known fallback option
- LLMs are NOT used for per-document processing, search ranking, or metadata extraction — those are handled by classical methods

### Job queue
- **Taskiq** (modern async Python task queue) backed by **Redis 7**

### Infrastructure
- **Hetzner VPS** (EU-hosted) as the primary compute
- **Docker + Docker Compose** for all services
- **Docker context** for clean local-to-remote deployment
- **Docker Compose file-based secrets** mounted at `/run/secrets/` (never environment variables for sensitive values)
- **Caddy** as reverse proxy with automatic HTTPS

### Observability
- **OpenTelemetry** instrumentation for traces and metrics
- **Prometheus + Grafana** (self-hosted) for metrics and dashboards
- **Loki** for log aggregation
- **Sentry** (self-hosted EU instance or EU tier) for error tracking
- **structlog** for structured JSON logs with request ID correlation

### Testing
- **pytest** + **pytest-asyncio** for Python tests
- **testcontainers-python** for real Postgres in integration tests
- **Playwright** for end-to-end tests
- **Hypothesis** for property-based tests
- **Factory Boy** for test fixtures

### CI/CD
- **GitHub Actions** for linting, type-checking, testing, building, and security scanning
- **pre-commit** framework with gitleaks, ruff, pyright, hadolint, conventional-commits hooks
- **Trivy** for container vulnerability scanning
- **Syft** for SBOM generation

## Alternatives considered

Key alternatives at each layer:

- **Vector database**: Qdrant, Milvus, Weaviate considered. pgvectorscale chosen because it matches or beats dedicated vector DBs at our scale (millions of vectors) while keeping everything in PostgreSQL.
- **Keyword search**: Elasticsearch, Meilisearch, OpenSearch, PostgreSQL tsvector considered. ParadeDB pg_search chosen because it provides real BM25 inside PostgreSQL without a separate service.
- **Document parsing**: Unstructured, LlamaParse, Marker considered. Docling chosen for best-in-class table extraction, self-hostable, and reasonable speed. Marker kept in mind as a fallback for complex PDFs.
- **Clustering**: naive K-means, hierarchical agglomerative, DBSCAN considered. BERTopic (UMAP + HDBSCAN) chosen because it's the modern state-of-the-art for document topic discovery and handles outliers natively.
- **Classification**: Naive Bayes, LinearSVC, XGBoost, fine-tuned BERT considered. Calibrated Logistic Regression chosen for best combination of accuracy, calibrated probabilities, explainability, and speed.
- **OCR**: Tesseract, PaddleOCR, Surya considered. EasyOCR (via Docling) chosen for deep learning quality without operational complexity; Surya kept as a future option if OCR becomes a bottleneck.
- **Embeddings**: Cohere, Voyage, OpenAI, BGE-M3 considered. Jina v4 chosen for EU hosting, open weights (escape hatch), and strong multilingual performance. BGE-M3 as known self-hosted fallback.
- **Auth**: Authentik, Ory Kratos, custom JWT considered. FastAPI-Users chosen for tight FastAPI integration and manageable operational complexity. Authentik remains an upgrade path if SSO becomes required.
- **Task queue**: ARQ, Celery, Dramatiq, SAQ considered. Taskiq chosen for modern async FastAPI integration and active maintenance.
- **Frontend styling**: Tailwind, UnoCSS, PandaCSS, StyleX considered. Open Props + Svelte scoped styles + Bits UI chosen for full control, Svelte-native approach, and avoiding framework lock-in.

## Consequences

**Positive:**

- The stack is cohesive: Python throughout the backend, Svelte/TypeScript throughout the frontend, PostgreSQL as the single source of truth for relational data, vectors, and keyword search
- EU-hostable end to end with clear fallback paths for every third-party service
- Classical-first architecture means dramatically lower LLM costs (target: <€1/month per org) and better explainability
- Professional tools at every layer (uv, Ruff, pyright, pgBackRest, OpenTelemetry) mean no shortcuts on quality
- pgvectorscale and pg_search provide state-of-the-art search performance without the operational burden of separate vector and search databases

**Negative:**

- Large stack surface area — many tools to keep current and understand
- pgvectorscale and ParadeDB pg_search are newer extensions with smaller communities than core PostgreSQL features (mitigated by both being production-ready with active development)
- Docling's Swedish OCR quality is unknown until tested on real documents (mitigated by fallback to EasyOCR and potential Surya upgrade)
- KB-BERT NER requires Hugging Face transformers infrastructure (heavier than pure spaCy but genuinely better for Swedish)
- BERTopic is a higher-level abstraction that may need to be replaced with direct UMAP + HDBSCAN + scikit-learn if we need finer control

**Neutral:**

- Commit to Python + PostgreSQL + Docker monoculture on the backend — simplifies operations but limits polyglot flexibility, which is acceptable given the project's scope
- Stack is heavier than a minimal "RAG with OpenAI API" approach, but that's deliberate: the project's differentiation depends on this architecture

## Review criteria

This decision should be revisited if:

- A core component proves unviable in practice (e.g., pgvectorscale compatibility issues, Docling failing on target documents at scale)
- A significantly better alternative emerges and can be adopted without major rewrite
- The project's scope shifts substantially

Review cadence: informal review at the end of each implementation phase, formal review at the 12-month mark.
