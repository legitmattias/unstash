# Retrieval evaluation harness

Offline evaluation of the hybrid search pipeline against a committed, synthetic
Swedish-BRF golden set. Ingests the corpus into a fresh Postgres testcontainer
at migration head, runs vector / BM25 / RRF configurations over the golden
queries, and reports Recall@10, MRR, and nDCG@10 — with clustered confidence
intervals and a paired permutation test (`metrics.py`).

- `run_eval.py` — the runner (`--embedder jina` for the real measurement,
  `--embedder fake` for a deterministic plumbing check; `--json` emits metrics).
- `eval_gate.py` — the CI regression gate: deterministic run vs a pinned
  baseline, failing on a drop beyond tolerance. `--calibrate N` measures the
  run-to-run spread and writes the baseline.
- `corpus/`, `golden.jsonl`, `world_bible.md` — the synthetic documents, graded
  queries, and the fictional BRF they describe.
- `local_trace.py` — not part of this harness's measurement. Indexes an
  operator-provided directory and dumps one search trace per query for hand-read
  error analysis. It reports **no metrics**: those queries carry no relevance
  judgments, so any score computed over them would be invented.

## Golden set version: **v1** (46 documents, 44 queries)

The corpus and golden set are versioned as a unit. **Any content change is a
re-baseline event:**

- Component E's pinned baseline (`baseline-deterministic.json`) is tied to v1.
  Change the corpus and the baseline is stale — re-run `--calibrate` and re-pin.
- Every experiment report (`reports/`) is relative to v1. A v2 corpus is **never
  comparable to v1 numbers.** Do not mix numbers across versions; bump to v2 and
  re-run the affected decisions.

## What v1 is good for, and what it is not

Effective sample size under the clustered bootstrap is **distinct documents, not
queries** — 23 overall, 4-7 per slice.

- **CI regression gate (the overall metric): adequate.** 23 clusters against a
  pinned baseline catch real pipeline regressions.
- **Per-slice architectural decisions: under-powered.** The `decision` slice is
  7 clusters and `english` is below the interval floor. Slice-level comparisons
  sit inside the noise; growing the *corpus* (not the query count) is the lever.

Open follow-ups: adversarial slice + fusion re-sweep (#159), per-slice corpus
growth + v2 re-baseline (#160), refusal-path metric (#149).
