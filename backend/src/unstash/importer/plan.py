"""Decide which files under a folder are imported, and under what path."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# Files that operating systems and sync or office clients create beside
# real documents. Compared case-insensitively against the file name.
_IGNORED_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "icon\r"})
# "~$" marks an Office lock file, "._" a macOS resource fork, ".~lock."
# a LibreOffice lock file.
_IGNORED_PREFIXES = ("~$", "._", ".~lock.")

_FALLBACK_MIME = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ImportItem:
    """One file selected for import."""

    file: Path
    source_path: str
    modified_at: datetime
    size_bytes: int
    mime_type: str


def is_ignored(name: str) -> bool:
    """Return True for file names that are never documents."""
    return name.lower() in _IGNORED_NAMES or name.startswith(_IGNORED_PREFIXES)


def iter_import_items(root: Path, prefix: str = "") -> Iterator[ImportItem]:
    """Yield every importable file under ``root`` in sorted path order.

    ``source_path`` is the file's path relative to ``root`` with forward
    slashes, optionally under ``prefix``. Symbolic links are skipped,
    both files and directories, so the walk cannot leave ``root`` or
    loop. Empty files are skipped. Hidden directories are skipped.
    """
    prefix_parts = [part for part in prefix.split("/") if part]
    for path in sorted(_walk(root)):
        stat = path.stat()
        if stat.st_size == 0:
            continue
        relative = path.relative_to(root)
        mime_type, _ = mimetypes.guess_type(path.name)
        yield ImportItem(
            file=path,
            source_path="/".join([*prefix_parts, *relative.parts]),
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            size_bytes=stat.st_size,
            mime_type=mime_type or _FALLBACK_MIME,
        )


def _walk(directory: Path) -> Iterator[Path]:
    for entry in directory.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if not entry.name.startswith("."):
                yield from _walk(entry)
        elif entry.is_file() and not is_ignored(entry.name):
            yield entry
