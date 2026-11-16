"""Import a local folder: ``python -m unstash.importer --help``."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import httpx

from unstash.importer.client import Outcome, upload_item
from unstash.importer.plan import iter_import_items

# Generous read timeout: the server hashes and stores the whole upload
# before it answers.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m unstash.importer",
        description=(
            "Upload every file under a folder to one organisation, recording "
            "each file's path relative to the folder and its modification "
            "time. Files already present are not ingested again; their path "
            "is added to the existing document, so the command can be "
            "repeated safely."
        ),
    )
    parser.add_argument("root", type=Path, help="Folder to import.")
    parser.add_argument("--url", required=True, help="Base URL of the service.")
    parser.add_argument("--org", required=True, help="Organisation slug.")
    parser.add_argument(
        "--token-file",
        type=Path,
        required=True,
        help="File containing an API token with access to the organisation.",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="Path placed in front of every recorded source path.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many files.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be uploaded without contacting the service.",
    )
    return parser.parse_args(argv)


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    """Run the import; return 0 only if no file was rejected or failed."""
    args = _parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        sys.stderr.write(f"error: {root} is not a directory\n")
        return 2

    items = iter_import_items(root, args.prefix)
    if args.dry_run:
        count = 0
        total_bytes = 0
        for item in items:
            if args.limit is not None and count >= args.limit:
                break
            _emit(f"{item.size_bytes:>12}  {item.source_path}")
            count += 1
            total_bytes += item.size_bytes
        _emit(f"{count} files, {total_bytes} bytes")
        return 0

    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        sys.stderr.write(f"error: {args.token_file} is empty\n")
        return 2

    outcomes: Counter[Outcome] = Counter()
    with httpx.Client(
        base_url=args.url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    ) as client:
        for index, item in enumerate(items):
            if args.limit is not None and index >= args.limit:
                break
            outcome = upload_item(client, args.org, item)
            outcomes[outcome] += 1
            _emit(f"{outcome.value:<10} {item.source_path}")

    _emit(", ".join(f"{outcomes[outcome]} {outcome.value}" for outcome in Outcome))
    return 1 if outcomes[Outcome.REJECTED] or outcomes[Outcome.FAILED] else 0


if __name__ == "__main__":
    raise SystemExit(main())
