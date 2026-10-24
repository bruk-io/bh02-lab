import io
import tarfile

import pytest

from bh02_lab.hashing import Entry, tree_digest
from bh02_lab.snapshot import SnapshotError, entries_of, packed

FILES = [Entry("src/a.py", False, b"print(1)\n"), Entry("run.sh", True, b"#!/bin/sh\n")]


def _tar(members: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, content in members:
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def test_the_hash_ignores_order_and_caches():
    cached = Entry("src/__pycache__/a.cpython-315.pyc", False, b"junk")
    assert tree_digest(FILES) == tree_digest([*reversed(FILES), cached])


def test_the_hash_sees_content_mode_and_path():
    base = tree_digest(FILES)
    assert tree_digest([Entry("src/a.py", False, b"print(2)\n"), FILES[1]]) != base
    assert tree_digest([Entry("src/a.py", True, FILES[0].content), FILES[1]]) != base
    assert tree_digest([Entry("src/b.py", False, FILES[0].content), FILES[1]]) != base


def test_packing_is_canonical_and_round_trips():
    tar = packed(FILES)
    assert tar == packed(list(reversed(FILES)))
    assert sorted(entries_of(tar), key=lambda e: e.path) == sorted(FILES, key=lambda e: e.path)


def test_a_link_is_left_out():
    link = tarfile.TarInfo("evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    assert entries_of(_tar([(link, b""), (tarfile.TarInfo("ok.py"), b"x")])) == [Entry("ok.py", False, b"x")]


def test_a_virtual_environment_is_left_out():
    assert entries_of(_tar([(tarfile.TarInfo(".venv/bin/python"), b"elf")])) == []


@pytest.mark.parametrize("name", ["/etc/passwd", "../outside.py", "a/../../b"])
def test_a_path_outside_the_workspace_is_refused(name: str):
    with pytest.raises(SnapshotError, match="not a path inside"):
        entries_of(_tar([(tarfile.TarInfo(name), b"x")]))


def test_a_workspace_over_the_limit_is_refused():
    with pytest.raises(SnapshotError, match="over 4 bytes"):
        entries_of(_tar([(tarfile.TarInfo("big"), b"12345")]), max_bytes=4)


def test_garbage_is_refused():
    with pytest.raises(SnapshotError, match="can't be read"):
        entries_of(b"not a tar at all" * 100)
