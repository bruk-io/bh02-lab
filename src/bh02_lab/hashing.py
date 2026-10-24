"""Content hashes for what crosses from the untrusted rollout to the trusted side.

A workspace is hashed from its files' paths, executable bits and contents, never from metadata
the sandbox controls (times, owners), so two rollouts that leave the same files have the same
snapshot hash. What bh-02 and Python keep for themselves (caches) is left out, so it neither
changes the hash nor reaches the judge.

Pure: entries in, hex digest out. Reading a directory is `snapshot.py`'s.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

__all__ = ["Entry", "digest", "kept", "tree_digest"]

# Directories a run leaves that are not the work: caches, environments and version control.
_SKIPPED_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "mypy_cache",
        ".git",
        ".venv",
        "venv",
        "node_modules",
    }
)


@dataclass(frozen=True, slots=True)
class Entry:
    """A file in a workspace: its path relative to the workspace root, whether it is
    executable, and its bytes."""

    path: str
    executable: bool
    content: bytes


def digest(data: bytes) -> str:
    """sha256 of bytes, as hex.

    >>> digest(b"")[:12]
    'e3b0c44298fc'
    """
    return hashlib.sha256(data).hexdigest()


def kept(path: str) -> bool:
    """Whether a workspace file is part of the snapshot: not under a cache directory.

    >>> kept("src/app.py"), kept("src/__pycache__/app.cpython-315.pyc"), kept(".bh-02/plugins/x.py")
    (True, False, True)
    """
    return not any(part in _SKIPPED_DIRS for part in PurePosixPath(path).parts[:-1])


def tree_digest(entries: Iterable[Entry]) -> str:
    """The workspace's hash: one line per kept file, sorted by path, of its mode, its content's
    hash and its path, hashed together. Order of `entries` does not matter.

    >>> a = Entry("a.py", False, b"x")
    >>> b = Entry("b.sh", True, b"y")
    >>> tree_digest([a, b]) == tree_digest([b, a])
    True
    """
    lines = sorted(
        f"{'755' if entry.executable else '644'} {digest(entry.content)} {entry.path}"
        for entry in entries
        if kept(entry.path)
    )
    return digest("\n".join(lines).encode())
