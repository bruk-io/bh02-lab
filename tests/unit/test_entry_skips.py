from pathlib import Path

from bh02_lab import snapshot
from bh02_lab.hashing import kept
from bh02_lab.sandbox import entry


def test_what_the_sandbox_leaves_out_the_snapshot_leaves_out_too() -> None:
    assert all(not kept(f"{name}/x.py") for name in entry.SKIPPED_DIRS)
    assert kept("src/x.py")


def test_the_sandbox_packs_under_the_snapshots_caps() -> None:
    assert (entry.MAX_FILES, entry.MAX_BYTES) == (snapshot.MAX_FILES, snapshot.MAX_BYTES)


def test_a_deep_tree_is_oversized_not_a_crash(tmp_path: Path) -> None:
    deep = tmp_path.joinpath(*["d"] * (entry.MAX_DEPTH + 5))
    deep.mkdir(parents=True)
    (deep / "x.py").write_text("x")
    assert entry.packable(tmp_path) == ([], f"deeper than {entry.MAX_DEPTH} directories")


def test_too_many_files_is_oversized(tmp_path: Path) -> None:
    for i in range(entry.MAX_FILES + 1):
        (tmp_path / f"f{i}").write_bytes(b"")
    assert entry.packable(tmp_path)[1] == f"over {entry.MAX_FILES} files"


def test_links_and_caches_are_left_out(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "link").symlink_to("/etc/passwd")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "big").write_bytes(b"0" * 10)
    assert [name for _, name in entry.packable(tmp_path)[0]] == ["a.py"]
