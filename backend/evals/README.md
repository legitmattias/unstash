# Evaluation assets

Golden datasets and runners for measuring retrieval (and later classification)
quality. See the retrieval evaluation design in the M4 milestone plan.

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
  golden.jsonl          queries with relevance judgments (added with the harness)
```
