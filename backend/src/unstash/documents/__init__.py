"""Document ingestion helpers.

The HTTP surface lives in :mod:`unstash.documents.router` and is
imported from there directly; re-exporting it here would put the router
in the worker's import chain (circular import via the tasks package).
"""

from __future__ import annotations
