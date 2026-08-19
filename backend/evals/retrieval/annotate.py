"""Hand-annotation tool for retrieval traces, and the axial-coding step after it.

Error analysis is read by hand, one trace at a time, and the format it arrives
in decides whether that actually happens. A single long markdown file means
scrolling to find where one query ends and the next begins, and it offers
nowhere to put a judgement except the file itself.

Three subcommands:

    python evals/retrieval/annotate.py build --traces traces.jsonl --out review.html
    python evals/retrieval/annotate.py taxonomy --annotations annotations.jsonl
    python evals/retrieval/annotate.py render --traces traces.jsonl --out traces.md

``build`` produces a **self-contained HTML file** — data embedded, no server, no
dependencies, opens with a double click. Judgements are held in the browser's
local storage as you go and exported as JSONL when you are done.

``taxonomy`` is the axial-coding pass: it groups the exported notes, counts
them, and writes the failure taxonomy that error analysis exists to produce.

``render`` writes a readable markdown dump. It is a view of the structured
traces and never a source for them — the data flows one way.

## Why the tool is shaped this way

The method is open coding from grounded theory, and two of its rules drove the
design:

- **Free text first, no predefined categories.** Offering a list of failure
  types to tick would decide the answer before the material is read; everything
  that did not fit would land in "other" and vanish. The note field is
  therefore the primary control, and previously used notes are offered only as
  autocomplete — reuse emerges from the material rather than being imposed.
- **First upstream failure only.** Failures cascade: a document that was never
  indexed is also ranked wrongly and snippeted wrongly. Recording each
  consequence separately makes downstream symptoms dominate the counts. The
  form takes exactly one note per query for that reason.

Marking which results were actually relevant is offered alongside, because the
judgement is being made anyway while reading — and those marks are a golden set
being built as a by-product, at the point where it is nearly free.

The output file contains real document text and belongs outside version
control, like the traces it is built from.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "annotate_template.html"


def load_run(traces: Path) -> tuple[list[dict], dict, list[dict]]:
    """Load the three files a trace run produces.

    Only ``traces.jsonl`` is required. The run metadata carries the
    document-to-paths map, and the manifest says what became of every corpus
    file; without them the page still works, but a reviewer loses the ability
    to tell a retrieval miss from a document that was never indexed.
    """
    records = [
        json.loads(line) for line in traces.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    meta_path = traces.with_name(traces.stem + "-run.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    manifest_path = traces.with_name(traces.stem + "-manifest.jsonl")
    manifest = (
        [
            json.loads(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if manifest_path.exists()
        else []
    )
    return records, meta, manifest


def build(traces: Path, out: Path, corpus_root: str | None) -> None:
    """Write a self-contained annotation page for a trace run."""
    if not TEMPLATE.exists():
        sys.exit(f"template missing: {TEMPLATE}")
    records, meta, manifest = load_run(traces)
    if not records:
        sys.exit(f"no records in {traces} — is it a traces.jsonl from local_trace.py?")

    payload = {
        "source": str(traces),
        "queries": records,
        "documents": meta.get("documents", {}),
        "corpus": manifest,
        # Absolute root, so the page can turn a stored relative path into a
        # file:// link and into something worth copying into a terminal.
        "corpusRoot": corpus_root or meta.get("corpus_dir", ""),
        "meta": {k: v for k, v in meta.items() if k not in ("documents",)},
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null",
        json.dumps(payload, ensure_ascii=False),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    hits = sum(len(r["hits"]) for r in records)
    print(f"{len(records)} queries, {hits} results -> {out}")
    if manifest:
        counts = collections.Counter(row["status"] for row in manifest)
        print("corpus lookup: " + ", ".join(f"{n} {s}" for s, n in counts.most_common()))
    else:
        print(
            "no manifest beside the traces: the lookup cannot say whether a file "
            "was indexed, deduplicated or skipped"
        )
    print("open it in a browser; judgements are kept in local storage as you go")


def render(traces: Path, out: Path) -> None:
    """Render a readable markdown dump from the structured traces.

    A view, never a source. An earlier version had this the other way round —
    markdown written first and parsed back with regular expressions — which
    silently dropped 28% of results the day rerank scores turned out to be
    signed.
    """
    records, meta, _ = load_run(traces)
    documents = meta.get("documents", {})
    lines = [f"# Retrieval traces — {len(records)} queries", ""]
    for record in records:
        lines += [f"## {record['n']}. {record['query']}", ""]
        if record.get("failed"):
            lines += [f"**Query failed:** {record['failed']}", "", "---", ""]
            continue
        lines.append(
            f"`results={record['results']}` `reranked={record['reranked']}` "
            f"`bm25={record['bm25_used']}`"
        )
        lines.append("")
        for hit in record["hits"]:
            paths = documents.get(hit["document_id"], {}).get("paths", ["?"])
            rerank = "—" if hit["rerank"] is None else f"{hit['rerank']:.3f}"
            legs = (f"vector #{hit['vector_rank']}" if hit["vector_rank"] else "vector —") + (
                f" · bm25 #{hit['bm25_rank']}" if hit["bm25_rank"] else " · bm25 —"
            )
            lines += [
                f"**{hit['rank']}. {hit['title']}** · `{hit['mime']}` · "
                f"fused {hit['fused']:.4f} · rerank {rerank}",
                f"  {legs} · fused #{hit['fused_rank']} → shown #{hit['rank']}",
                f"  `{paths[0]}`"
                + (f" (+{len(paths) - 1} more filings)" if len(paths) > 1 else ""),
                f"  > {' '.join(hit['snippet'].split())}",
                "",
            ]
        lines += ["---", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"written to {out}")


def taxonomy(annotations: Path, out: Path | None) -> None:
    """Group exported judgements into a counted failure taxonomy.

    Grouping is by exact note text, which is why the page offers previous notes
    as autocomplete: consistent wording is what makes the counts mean anything.
    Synonyms are merged by hand afterwards — the grouping is the judgement, the
    arithmetic is not.
    """
    rows = [
        json.loads(line)
        for line in annotations.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        sys.exit("no rows in the export")
    judged = [r for r in rows if r.get("note") or r.get("relevant") or r.get("expected_path")]
    if not judged:
        sys.exit("nothing judged yet")

    lines = [
        "# Failure taxonomy",
        "",
        f"{len(judged)} judged of {len(rows)} reviewed.",
        "",
        "Counts are by exact note text. Merge synonymous notes by hand before "
        "treating these as final.",
        "",
    ]
    lines += _note_table(judged)
    lines += _stage_table(judged)
    lines += _leg_contribution(rows)
    lines += [
        "## Prioritise by count times severity",
        "",
        "These counts say what is *frequent*. They do not say what is *damaging*. "
        "A rare confidently-wrong answer outranks a common empty result — rank the "
        "list by hand before deciding what to build.",
    ]
    report = "\n".join(lines) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"written to {out}")
    else:
        print(report)


def _note_table(judged: list[dict]) -> list[str]:
    notes = collections.Counter(r["note"].strip() for r in judged if (r.get("note") or "").strip())
    if not notes:
        return ["## By note", "", "_No notes written._", ""]
    return [
        "## By note — the mechanism",
        "",
        "| count | note |",
        "|---:|---|",
        *[f"| {count} | {note} |" for note, count in notes.most_common()],
        "",
    ]


def _stage_table(judged: list[dict]) -> list[str]:
    stages = collections.Counter(r["stage"] for r in judged if r.get("stage"))
    if not stages:
        return [
            "## By stage",
            "",
            "_No expected documents named, so no stage could be computed._ The stage "
            "is derived from the trace once a reviewer says which document should "
            "have won; without that it is not recoverable.",
            "",
        ]
    return [
        "## By stage — where it broke",
        "",
        "Computed from the trace, not judged by hand.",
        "",
        "| count | stage |",
        "|---:|---|",
        *[f"| {count} | {stage} |" for stage, count in stages.most_common()],
        "",
    ]


def _leg_contribution(rows: list[dict]) -> list[str]:
    """How often each retrieval leg actually supplied a relevant document.

    Reciprocal rank fusion works on ranks alone, so a leg that returned nothing
    relevant still contributes its top result at full strength. Per query that
    is indistinguishable from a reranking failure; across a set it is
    measurable, and it is the direct test of whether the keyword leg earns its
    place on this corpus — the same question decompounding is meant to answer.
    """
    relevant_shown = 0
    from_vector = from_bm25 = from_both = 0
    queries_with_relevant = 0
    bm25_contributed = 0
    for row in rows:
        marked = {m["path"] for m in row.get("relevant", [])}
        if not marked:
            continue
        queries_with_relevant += 1
        contributed = False
        for hit in row.get("shown", []):
            if hit["path"] not in marked:
                continue
            relevant_shown += 1
            has_v, has_b = hit.get("vector_rank"), hit.get("bm25_rank")
            if has_v and has_b:
                from_both += 1
            elif has_v:
                from_vector += 1
            elif has_b:
                from_bm25 += 1
            if has_b:
                contributed = True
        if contributed:
            bm25_contributed += 1

    if not relevant_shown:
        return [
            "## Leg contribution",
            "",
            "_No relevant results marked, so no contribution could be computed._",
            "",
        ]
    share = f"{bm25_contributed}/{queries_with_relevant}"
    return [
        "## Leg contribution — is each leg earning its place?",
        "",
        f"Of {relevant_shown} relevant results marked across {queries_with_relevant} queries:",
        "",
        "| found by | count |",
        "|---|---:|",
        f"| both legs | {from_both} |",
        f"| vector only | {from_vector} |",
        f"| keyword only | {from_bm25} |",
        "",
        f"The keyword leg supplied at least one relevant result in **{share}** "
        "queries that had any.",
        "",
        "A low number here is the argument for decompounding, and the reason to "
        "re-examine the fusion weights: rank-based fusion cannot tell a leg that "
        "found nothing from one that found the answer, so an unhelpful leg still "
        "injects its top result at full strength.",
        "",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build_cmd = sub.add_parser("build", help="make the annotation page")
    build_cmd.add_argument("--traces", type=Path, required=True)
    build_cmd.add_argument("--out", type=Path, required=True)
    build_cmd.add_argument(
        "--corpus-root",
        help="Absolute corpus path for file:// links. Defaults to the one "
        "recorded in the run metadata.",
    )

    render_cmd = sub.add_parser("render", help="markdown dump of the traces")
    render_cmd.add_argument("--traces", type=Path, required=True)
    render_cmd.add_argument("--out", type=Path, required=True)

    tax_cmd = sub.add_parser("taxonomy", help="group exported notes into counts")
    tax_cmd.add_argument("--annotations", type=Path, required=True)
    tax_cmd.add_argument("--out", type=Path)

    args = parser.parse_args()
    if args.command == "build":
        build(args.traces, args.out, args.corpus_root)
    elif args.command == "render":
        render(args.traces, args.out)
    else:
        taxonomy(args.annotations, args.out)


if __name__ == "__main__":
    main()
