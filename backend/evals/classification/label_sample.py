"""Blind labelling CLI for building a document-type gold set.

Samples documents from a corpus directory, pre-fetches a short text
snippet for each, then walks them one at a time and records the operator's
type judgement to a local JSONL file.

**Blind by design**: no rule prediction is ever displayed. This gold set is
used to score filename/path rules, and showing those rules' guesses would
contaminate the measurement. Pre-annotation is documented as time-saving
and unbiased for general annotation work, but not for scoring the
annotator's own prompt.

Resumable: documents already present in the output file are skipped, so the
session can be stopped and continued at any point.

    python evals/classification/label_sample.py \
        --corpus-dir /path/to/documents --out labels.jsonl

Nothing leaves the machine: snippets come from local parsing, and the
output file stays wherever the operator puts it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(HERE))

from taxonomy import ADJUDICATION_ESCAPES, TYPES

SUFFIXES = {".pdf", ".docx", ".md", ".txt"}
SNIPPET_CACHE = Path.home() / ".cache" / "unstash-local-eval" / "snippet"
SNIPPET_CHARS = 400
# Families worth over-sampling: they are the cases where filename rules are
# most likely to be either very right or very wrong.
HARD_FAMILY_TERMS = ("energidekl", "anlaggning", "anläggning", "hus ", "protokoll")
HARD_FAMILY_QUOTA = 20


def _all_files(corpus_dir: Path) -> list[Path]:
    return sorted(p for p in corpus_dir.rglob("*") if p.suffix.lower() in SUFFIXES)


def _sample(files: list[Path], size: int, seed: int) -> list[Path]:
    """Seeded random sample, topped up with known-hard filename families."""
    rng = random.Random(seed)
    hard = [p for p in files if any(t in p.name.lower() for t in HARD_FAMILY_TERMS)]
    chosen: list[Path] = []
    if hard:
        chosen.extend(rng.sample(hard, min(HARD_FAMILY_QUOTA, len(hard))))
    remaining = [p for p in files if p not in set(chosen)]
    chosen.extend(rng.sample(remaining, min(size - len(chosen), len(remaining))))
    rng.shuffle(chosen)
    return chosen


async def _snippet(path: Path) -> str:
    """First lines of extracted text, cached on disk by file content."""
    from unstash.documents.parser import parse_to_chunks

    digest = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    cached = SNIPPET_CACHE / f"{digest}.txt"
    if cached.exists():
        return await asyncio.to_thread(cached.read_text, "utf-8")
    try:
        parsed = await asyncio.to_thread(parse_to_chunks, path)
        text = parsed.chunks[0].text[:SNIPPET_CHARS] if parsed.chunks else ""
    except Exception as exc:
        text = f"[could not parse: {type(exc).__name__}]"
    await asyncio.to_thread(cached.write_text, text, "utf-8")
    return text


def _load_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    done: set[str] = set()
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(json.loads(line)["relpath"])
    return done


def _print_menu() -> None:
    options = [*TYPES, *ADJUDICATION_ESCAPES]
    width = max(len(o) for o in options) + 4
    print("\n  choices:")
    for index, name in enumerate(options, start=1):
        end = "\n" if index % 3 == 0 else ""
        print(f"    {index:>2}. {name:<{width}}", end=end)
    print("\n    m = more text   s = skip   q = save and quit")


async def main(corpus_dir: Path, out_path: Path, size: int, seed: int) -> None:
    SNIPPET_CACHE.mkdir(parents=True, exist_ok=True)
    files = _all_files(corpus_dir)
    if not files:
        sys.exit(f"no supported documents under {corpus_dir}")

    sample = _sample(files, size, seed)
    done = _load_done(out_path)
    todo = [p for p in sample if str(p.relative_to(corpus_dir)) not in done]
    print(f"sample {len(sample)} documents; {len(done)} already labelled; {len(todo)} to go")

    if todo:
        print("pre-fetching snippets (one-off; cached afterwards) ...", flush=True)
        for index, path in enumerate(todo, start=1):
            await _snippet(path)
            if index % 10 == 0:
                print(f"  {index}/{len(todo)}", flush=True)

    options = [*TYPES, *ADJUDICATION_ESCAPES]
    with out_path.open("a", encoding="utf-8") as out:
        for index, path in enumerate(todo, start=1):
            relpath = str(path.relative_to(corpus_dir))
            snippet = await _snippet(path)
            print("\n" + "=" * 72)
            print(f"[{index}/{len(todo)}]  {path.name}")
            print(f"  path: {relpath}")
            print(f"  text: {snippet[:200].replace(chr(10), ' ')}")
            _print_menu()

            while True:
                answer = (await asyncio.to_thread(input, "  > ")).strip().lower()
                if answer == "q":
                    print(f"saved to {out_path}")
                    return
                if answer == "s":
                    break
                if answer == "m":
                    print(f"\n  {snippet}")
                    continue
                if answer.isdigit() and 1 <= int(answer) <= len(options):
                    out.write(
                        json.dumps(
                            {
                                "relpath": relpath,
                                "name": path.name,
                                "label": options[int(answer) - 1],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    out.flush()
                    break
                print("  ? enter a number, or m / s / q")

    print(f"\ndone; labels in {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("labels.jsonl"))
    parser.add_argument("--sample-size", type=int, default=150)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    asyncio.run(main(args.corpus_dir, args.out, args.sample_size, args.seed))
