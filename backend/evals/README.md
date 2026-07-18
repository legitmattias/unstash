# Evaluation assets

Golden datasets and runners for measuring retrieval (and later classification)
quality.

**Data governance:** everything under this tree is **fully synthetic** — a
fictional housing cooperative with invented people, companies, amounts, and
events, generated from `retrieval/world_bible.md` by `retrieval/generate_corpus.py`
and hand-curated. No real organisation's documents, names, or data appear here,
and none may be added. Real-corpus checks are run locally only and are never
committed.

Layout:

```
retrieval/
  world_bible.md        the fictional facts every document must be consistent with
  generate_corpus.py    regenerates corpus/ from the bible (Mistral; anchors verified)
  corpus/               the committed synthetic documents (markdown)
  golden.jsonl          queries with graded relevance judgments
```

## Golden set conventions

One JSON object per line: `id`, `query`, `category`, `relevant` (list of
`{doc, grade}`), optional `notes`.

- **Grades:** 3 = the answer document; 2 = substantially answers; 1 =
  contextually relevant; unlisted = 0. nDCG uses the grades; Recall@k and MRR
  treat grade ≥ 2 as relevant.
- **Categories** (`decision`, `keyword`, `semantic`, `numeric`, `date`,
  `english`, `no_answer`) allow per-category ablation slices — e.g. stemming
  should move `semantic`/`keyword` Swedish queries, rerank should move
  `decision` ordering.
- **`no_answer` queries** have empty judgments; they are excluded from ranking
  averages and reserved for future confidence-threshold work.
- Judgments are at **document level**; the runner maps them to chunks at eval
  time. Judgment completeness was reviewed against the whole corpus (every
  query checked against plausible accidental answers).
- The set will be **enriched from real logged queries** (`search_logs`) once
  pilot usage exists — imagination is the bootstrap, not the source of truth.
