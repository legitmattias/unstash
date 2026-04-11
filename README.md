# Unstash

Adaptive document search and organization for Swedish organizations.

Unstash makes an organization's documents searchable via natural language, with automatic categorization discovered from the actual documents — no manual tagging, no predefined taxonomies. Primary target: BRFs (Swedish housing cooperatives) and small professional services firms.

## Status

**Pre-MVP.** Project bootstrap in progress.

## Technical highlights

Unstash is built on a **hybrid classical + LLM architecture** that uses classical information retrieval and machine learning as the default, with LLMs reserved for tasks where they're uniquely valuable:

- **Hybrid retrieval** — vector search (pgvectorscale) + BM25 (ParadeDB pg_search), combined via Reciprocal Rank Fusion, all in PostgreSQL
- **Automatic categorization** — BERTopic (UMAP + HDBSCAN + c-TF-IDF) discovers document types per organization, labeled once via LLM
- **Classical classification** — calibrated logistic regression on Jina embeddings + TF-IDF features for ongoing categorization
- **Universal metadata** — dates, people, organizations, and amounts extracted via KB-BERT NER and regex, independent of document type
- **Swedish-first** — PostgreSQL Swedish stemmer for keyword search, Jina multilingual embeddings, Swedish NER from KBLab
- **Surgical LLM use** — Mistral Large 2 (EU-hosted) for cluster labeling and rare classification fallbacks; optional answer synthesis as a future premium feature

This design is intentionally not a generic RAG wrapper. See the planning documents in the companion repo for the full rationale.

## Stack

- **Frontend:** SvelteKit + TypeScript, Open Props + Svelte scoped styles + Bits UI
- **Backend:** FastAPI + Python 3.12+
- **Database:** PostgreSQL 17 with pgvector, pgvectorscale, and ParadeDB pg_search
- **Parsing:** Docling with EasyOCR and LibreOffice headless
- **Embeddings:** Jina AI v4 (EU-hosted, open weights)
- **Classical ML:** scikit-learn, BERTopic, spaCy, Hugging Face transformers
- **Reranking:** BGE-reranker-v2-m3
- **LLM (surgical):** Mistral Large 2
- **Job queue:** Taskiq + Redis
- **Infrastructure:** Docker Compose on Hetzner, behind Caddy

## Planning and documentation

Detailed architecture, engineering standards, roadmap, and strategic analysis live in the companion planning repository. The source code repository contains only production code, tests, configurations, deployment scripts, ADRs, and operational runbooks.

## License

TBD — currently all rights reserved until a license is chosen.
