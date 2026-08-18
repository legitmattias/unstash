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
- `annotate.py` + `annotate_template.html` — the tooling for reading those
  traces and turning them into a counted failure taxonomy. See
  [Error analysis workflow](#error-analysis-workflow).

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

---

# Error analysis workflow

Reading traces by hand is the step that produces the specification everything
else is built against. **You cannot automate the discovery of failure modes you
do not yet know exist** — automation needs you to say what to look for, and
finding out what to look for is the entire point.

Three commands, in order.

## 1. Produce traces

```
JINA_API_KEY=... MISTRAL_API_KEY=... \
python evals/retrieval/local_trace.py \
    --corpus-dir /path/to/documents \
    --queries    /path/to/queries.txt \
    --out        /path/to/run/traces.jsonl \
    --ocr --gotenberg-url http://localhost:3001 --tpm 80000
```

Parsing, OCR and embeddings are cached by file content, so a second run over a
mostly-unchanged directory costs minutes rather than hours.

Three files come out:

| File | Holds |
|---|---|
| `traces.jsonl` | one query per line: results **and** the intermediate rankings behind them |
| `traces-run.json` | what is true of the whole run, plus the document-to-paths map |
| `traces-manifest.jsonl` | what became of each corpus file — indexed, duplicate, failed, not-indexable |

The manifest is what lets a reviewer tell a retrieval miss from a document that
was never searchable. Those look identical in a result list and need opposite
fixes.

## 2. Build the review page

```
python evals/retrieval/annotate.py build \
    --traces /path/to/run/traces.jsonl \
    --out    /path/to/run/review.html
```

A self-contained HTML file — data embedded, no server, no dependencies. Open it
in a browser.

Each result carries its provenance: `vector #20 · bm25 #2 · fused #10 → shown #1`
says the keyword leg found it second, the semantic leg barely found it, fusion
buried it tenth, and the reranker pulled it to the top. None of that is visible
from the ranked list alone.

For a readable dump without a browser:

```
python evals/retrieval/annotate.py render \
    --traces /path/to/run/traces.jsonl \
    --out    /path/to/run/traces.md
```

Markdown is a **view** of the traces, never a source for them. An earlier
version had it the other way round, and the regular expressions that parsed it
back silently dropped 28% of results the day rerank scores turned out to be
signed.

## 3. Group what you wrote

```
python evals/retrieval/annotate.py taxonomy \
    --annotations /path/to/annotations.jsonl \
    --out         /path/to/run/taxonomy.md
```

## Using the page

| Key | Does |
|---|---|
| `Enter` | save the note and go to the next query |
| `←` `→` | previous / next query |
| `1`–`9` | toggle a result as relevant |
| `Esc` | leave the note field |
| `?` | help |

Clicking a result also toggles it. Progress is written to the browser's local
storage on every change.

## How to code a trace

**Write what first went wrong, in your own words.** Not a category — the
categories come afterwards, from what you wrote. The urge to start ticking boxes
after the third trace is the thing to resist: once categories exist in your
head, everything afterwards gets bent to fit them, and whatever does not fit
lands in "other" and disappears.

**Record only the first failure.** Failures cascade. A document that was never
indexed is also ranked wrongly and snippeted wrongly; writing down all three
makes downstream symptoms dominate the counts, and you conclude that snippets
are the problem when retrieval is. Find the earliest point where the system did
something it should not have, write that, stop.

**Leave the note blank when the results are fine.** A blank note is a data
point, not a skipped row.

**Mark relevant results as you go.** You are judging relevance anyway while
reading. Each mark becomes a `(query, document)` pair the golden set can use —
nearly free at the moment you are already looking, and expensive to reconstruct
later.

**Stop when you stop learning.** The header tracks *distinct notes* and *since
new* — queries since a note appeared that you had not written before. When that
second number stays high for twenty or so, new traces have stopped producing new
categories. That is saturation, and it is the signal to stop rather than a fixed
quota.

## Practical notes

- **Progress is keyed on the traces path**, not on the HTML file. Rebuilding the
  page from the same traces keeps your work; moving or renaming the HTML is
  safe. Renaming the traces file starts a fresh session.
- **Local storage is per browser.** Do not switch browsers mid-pass, and export
  before clearing site data.
- **Export early and often.** The JSONL download is the durable artifact; local
  storage is a convenience.
- The taxonomy step groups by **exact note text**, which is why the page offers
  your previous notes as autocomplete. Merge synonyms by hand afterwards —
  grouping is the judgement, the counting is not.
- Counts say what is *frequent*, not what is *damaging*. A rare confidently
  wrong answer outranks a common empty result. Rank by count × severity by hand
  before deciding what to build.

## Data handling

The traces, the review page and the exported annotations all reproduce real
document titles, paths and text. **They belong outside this repository**, with
the corpus they came from. Every path above is an argument for that reason: no
default writes anything into the repo, and nothing derived from a private corpus
should ever be committed here.
