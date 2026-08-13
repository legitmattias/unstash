"""Hand-annotation tool for retrieval traces, and the axial-coding step after it.

Error analysis is read by hand, one trace at a time, and the format it arrives
in decides whether that actually happens. A single long markdown file means
scrolling to find where one query ends and the next begins, and it offers
nowhere to put a judgement except the file itself.

Two subcommands:

    python evals/retrieval/annotate.py build --traces traces.md --out review.html
    python evals/retrieval/annotate.py taxonomy --annotations annotations.jsonl

``build`` produces a **self-contained HTML file** — data embedded, no server, no
dependencies, opens with a double click. Judgements are held in the browser's
local storage as you go and exported as JSONL when you are done.

``taxonomy`` is the axial-coding pass: it groups the exported notes, counts
them, and writes the failure taxonomy that error analysis exists to produce.

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
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "annotate_template.html"

# Blocks look like: "## 12. query text" then a metadata line, then results.
_BLOCK = re.compile(r"^## (\d+)\. (.+)$")
_META = re.compile(r"`results=(\d+)`\s+`reranked=(\w+)`\s+`bm25=(\w+)`")
# Rerank scores are signed: the cross-encoder emits negatives for poor
# matches, so a pattern without the minus silently drops ~28% of results
# and misreads the rest as positive.
_HIT = re.compile(r"^\*\*(\d+)\. (.+?)\*\* · `(.+?)` · fused (-?[0-9.]+) · rerank (—|-?[0-9.]+)$")
_PATH = re.compile(r"^\s+`(.+?)`(.*)$")
_SNIPPET = re.compile(r"^\s+> (.*)$")


def parse_traces(path: Path) -> tuple[list[dict], str]:
    """Parse a trace report into query records, plus the report header."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] = []
    queries: list[dict] = []
    current: dict | None = None
    hit: dict | None = None

    for line in lines:
        block = _BLOCK.match(line)
        if block:
            if current:
                queries.append(current)
            current = {
                "n": int(block.group(1)),
                "query": block.group(2).strip(),
                "results": 0,
                "reranked": None,
                "bm25": None,
                "hits": [],
                "failed": False,
            }
            hit = None
            continue
        if current is None:
            header.append(line)
            continue

        if line.startswith("**Query failed:**"):
            current["failed"] = True
            continue
        meta = _META.search(line)
        if meta:
            current["results"] = int(meta.group(1))
            current["reranked"] = meta.group(2) == "True"
            current["bm25"] = meta.group(3) == "True"
            continue
        found = _HIT.match(line)
        if found:
            hit = {
                "rank": int(found.group(1)),
                "title": found.group(2),
                "mime": found.group(3),
                "fused": float(found.group(4)),
                "rerank": None if found.group(5) == "—" else float(found.group(5)),
                "path": "",
                "extra": "",
                "snippet": "",
            }
            current["hits"].append(hit)
            continue
        if hit is not None:
            where = _PATH.match(line)
            if where and not hit["path"]:
                hit["path"] = where.group(1)
                hit["extra"] = where.group(2).strip()
                continue
            snippet = _SNIPPET.match(line)
            if snippet:
                hit["snippet"] = snippet.group(1)

    if current:
        queries.append(current)
    return queries, "\n".join(header).strip()


def build(traces: Path, out: Path) -> None:
    """Write a self-contained annotation page for ``traces``."""
    if not TEMPLATE.exists():
        sys.exit(f"template missing: {TEMPLATE}")
    queries, header = parse_traces(traces)
    if not queries:
        sys.exit(f"no query blocks found in {traces} — is it a trace report?")

    payload = {
        "source": str(traces),
        "header": header,
        "queries": queries,
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null",
        json.dumps(payload, ensure_ascii=False),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    total_hits = sum(len(q["hits"]) for q in queries)
    print(f"{len(queries)} queries, {total_hits} results -> {out}")
    print("open it in a browser; judgements are kept in local storage as you go")


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

    tax_cmd = sub.add_parser("taxonomy", help="group exported notes into counts")
    tax_cmd.add_argument("--annotations", type=Path, required=True)
    tax_cmd.add_argument("--out", type=Path)

    args = parser.parse_args()
    if args.command == "build":
        build(args.traces, args.out)
    else:
        taxonomy(args.annotations, args.out)


if __name__ == "__main__":
    main()
