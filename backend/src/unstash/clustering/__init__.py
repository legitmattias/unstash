"""Document clustering — discovery of per-org document types.

The engine (:mod:`unstash.clustering.engine`) is pure computation over
precomputed embeddings; the service layer owns loading documents, persisting
runs and clusters, and the re-cluster triggers.
"""
