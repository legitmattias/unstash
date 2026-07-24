# Experiment: refusal path over the no_answer slice

**Date:** 2026-07-24 · **Script:** `refusal_check.py` · **Setup:** jina-embeddings-v4 + weighted RRF (k=20, 3:1) + jina-reranker-v3 over the synthetic corpus (46 docs, 272 chunks); signal = top result's rerank score per query. Closes the refusal-metric item (#149).

## Results

no_answer slice (n=4), top scores: 0.0232, 0.0788, 0.0819, **0.2643**.
Answerable (n=40): min 0.0605, p25 0.2059, median 0.2824.

Threshold operating points (abstain when top score < t):

| t | no_answer refused | answerable wrongly refused |
|---|---|---|
| 0.08 | 1/4 | 1/40 |
| 0.12 | **3/4** | **1/40** |
| 0.26 | 4/4 | 17/40 |

## Findings

1. **Two distinct no_answer subclasses with different ceilings.** Three queries are *off-corpus* (no related content exists) and separate cleanly — a threshold ≈0.12 refuses all three at one false refusal in forty. The fourth ("Vad kostar en garageplats i månaden?") is *topically-related-but-unanswerable*: the corpus has a garage protocol, so the reranker correctly scores high topical relevance (0.264, inside the answerable range) for content that does not answer the question.
2. **Relevance scores measure topical match, not answer presence.** No threshold separates the second subclass; that requires answerability judgment (deferred with answer synthesis) or query understanding (e.g. a cost question checking for extracted amounts co-occurring with the topic terms — classical, no model).

## Decision

- **No product abstention feature now** — a 4-query slice cannot set a threshold responsibly, and subclass 2 is beyond thresholds regardless.
- **Golden set v2 (#160): grow the no_answer slice with the two subclasses explicitly labelled** (`no_answer_off_corpus` vs `no_answer_related`), so the viable threshold and the structural ceiling are measured separately.
- The refusal metric is reported separately from ranking metrics, as authored; re-run `refusal_check.py` whenever the reranker or fusion configuration changes.
