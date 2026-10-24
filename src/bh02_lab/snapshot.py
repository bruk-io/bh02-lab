"""A workspace as it crosses from the rollout sandbox: tar bytes the sandbox made, read on the
trusted side into entries, hashed, and packed again in one canonical form for the store.

The sandbox's tar is untrusted. Only regular files with relative paths inside the workspace are
read; a link, a device, an absolute path, a `..`, or a workspace over the size limit is refused,
so nothing the model arranged can point the judge outside the snapshot. The canonical tar has
fixed owners and times, so the same files always make the same bytes.

`entries_of` and `packed` work on bytes in memory; `read_tree` is the one function that reads a
directory.
"""

import io
import os
import tarfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from bh02_lab.hashing import Entry, kept

__all__ = ["SnapshotError", "entries_of", "packed", "read_tree"]

MAX_BYTES = 64 * 1024 * 1024


class SnapshotError(ValueError):
    """A workspace that can't be taken as a snapshot, and why."""


def entries_of(tar_bytes: bytes, max_bytes: int = MAX_BYTES) -> list[Entry]:
    """The kept files of a tar, in its order.

    Raises:
        SnapshotError: The tar can't be read, holds something other than regular files and
            directories, names a path outside the workspace, or is over `max_bytes` unpacked.
    """
    entries: list[Entry] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as archive:
            for member in archive:
                if member.isdir():
                    continue
                if not member.isreg():
                    raise SnapshotError(f"{member.name!r} is not a regular file; a snapshot holds only files")
                path = _relative(member.name)
                total += member.size
                if total > max_bytes:
                    raise SnapshotError(f"the workspace is over {max_bytes} bytes unpacked")
                file = archive.extractfile(member)
                content = file.read() if file is not None else b""
                if kept(path):
                    entries.append(Entry(path, bool(member.mode & 0o111), content))
    except tarfile.TarError as error:
        raise SnapshotError(f"the workspace tar can't be read: {error}") from error
    return entries


def packed(entries: Iterable[Entry]) -> bytes:
    """The canonical tar of `entries`: sorted by path, owner 0, time 0, mode 644 or 755."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for entry in sorted(entries, key=lambda e: e.path):
            info = tarfile.TarInfo(entry.path)
            info.size = len(entry.content)
            info.mode = 0o755 if entry.executable else 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(entry.content))
    return buffer.getvalue()


def read_tree(root: Path) -> list[Entry]:
    """The kept files under `root`, links not followed (a link is refused).

    Raises:
        SnapshotError: A path under `root` is a link or not a regular file.
    """
    entries: list[Entry] = []
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise SnapshotError(f"{relative!r} is not a regular file; a snapshot holds only files")
            if kept(relative):
                entries.append(Entry(relative, os.access(path, os.X_OK), path.read_bytes()))
    return entries


def _relative(name: str) -> str:
    path = PurePosixPath(name)
    parts = [part for part in path.parts if part not in ("", ".")]
    if path.is_absolute() or ".." in parts or not parts:
        raise SnapshotError(f"{name!r} is not a path inside the workspace")
    return "/".join(parts)
