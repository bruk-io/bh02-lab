from bh02_lab.hashing import kept
from bh02_lab.sandbox import entry


def test_what_the_sandbox_leaves_out_the_snapshot_leaves_out_too() -> None:
    assert all(not kept(f"{name}/x.py") for name in entry.SKIPPED_DIRS)
    assert kept("src/x.py")
