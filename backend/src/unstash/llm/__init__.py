"""LLM call plumbing shared by all LLM-consuming components (ADR 0011).

Provider access goes through per-component config-switched backends; this
package holds what those backends share — the per-call audit record.
"""

from __future__ import annotations

from unstash.llm.calls import record_llm_call

__all__ = ["record_llm_call"]
