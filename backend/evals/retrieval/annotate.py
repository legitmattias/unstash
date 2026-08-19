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
    """Group exported notes into a counted failure taxonomy.

    Grouping is by exact note text, which is why the page offers autocomplete:
    consistent wording here is what makes the counts mean anything. Near
    duplicates are listed together under a heading so they can be merged by
    hand — the roadmap's advice is to hand-group the first several dozen rather
    than trust an automatic clustering nobody has checked.
    """
    rows = [
        json.loads(line)
        for line in annotations.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    coded = [r for r in rows if (r.get("note") or "").strip()]
    if not coded:
        sys.exit("no coded rows — nothing to group yet")

    by_note = collections.Counter(r["note"].strip() for r in coded)
    by_stage = collections.Counter(r.get("stage") or "unset" for r in coded)
    relevant = sum(1 for r in rows if r.get("relevant"))

    lines = [
        "# Failure taxonomy",
        "",
        f"From {len(coded)} coded queries out of {len(rows)} reviewed.",
        "",
        "Counts are by exact note text. Merge synonymous notes by hand before "
        "treating these as final — grouping is the judgement, not the arithmetic.",
        "",
        "## By note",
        "",
        f"| {'count':>5} | note |",
        "|---:|---|",
    ]
    lines += [f"| {count:>5} | {note} |" for note, count in by_note.most_common()]
    lines += [
        "",
        "## By stage (where it first went wrong)",
        "",
        f"| {'count':>5} | stage |",
        "|---:|---|",
    ]
    lines += [f"| {count:>5} | {stage} |" for stage, count in by_stage.most_common()]
    lines += [
        "",
        "## Relevance marks collected",
        "",
        f"{relevant} queries have at least one result marked relevant. Those are "
        "(query, document) pairs usable as golden-set entries — the by-product of "
        "having judged relevance while reading anyway.",
        "",
        "## Prioritise by count times severity",
        "",
        "The counts above say what is frequent. They do not say what is damaging. "
        "A rare failure that returns a confident wrong answer outranks a common one "
        "that returns nothing — rank the list by hand before deciding what to build.",
    ]
    report = "\n".join(lines) + "\n"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"written to {out}")
    else:
        print(report)


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
