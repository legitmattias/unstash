"""Provider-specific clients for hosted inference APIs.

One module per provider. Generic interfaces (protocols, error types,
fakes, factories) live with their domain (``documents.embedder``,
``search.reranker``); modules here contain only the code that encodes a
specific provider's wire format.
"""
