"""Self-hosted inference service: embeddings and reranking over HTTP.

Runs as its own process so one copy of each model serves both the API
(query embedding, reranking) and the worker (chunk embedding). It holds
no credentials and no tenant data, and is reachable only on the internal
network.
"""
