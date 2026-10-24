import pytest

from bh02_lab.hashing import Entry
from bh02_lab.rollout_core import lab_failure, settled
from bh02_lab.snapshot import packed

TAR = packed([Entry("a.py", False, b"x = 1\n")])


def test_a_clean_run_keeps_its_ending_and_files() -> None:
    entries, ended = settled(b'{"ended": "budget"}', TAR, False)
    assert ended == "budget" and [e.path for e in entries] == ["a.py"]


@pytest.mark.parametrize("result", [b'{"ended": "passed all tests!"}', b"not json", b"[]", b'{"ended": 3}', None])
def test_an_ending_it_does_not_know_is_a_crash(result: bytes | None) -> None:
    assert settled(result, TAR, False)[1] == "crashed"


def test_no_workspace_after_bh02_started_is_a_crash_not_a_lab_error() -> None:
    assert settled(b'{"ended": "replied"}', None, False) == ([], "crashed")


@pytest.mark.parametrize(
    ("result", "tar", "over"),
    [
        (b'{"ended": "oversized"}', None, False),
        (b'{"ended": "replied"}', TAR, True),
        (b'{"ended": "replied"}', b"x" * 700, False),
    ],
)
def test_a_workspace_over_the_caps_is_oversized(result: bytes, tar: bytes | None, over: bool) -> None:
    assert settled(result, tar, over) == ([], "oversized")


@pytest.mark.parametrize(
    ("prepared", "before", "after"), [(1, True, True), (None, True, True), (0, False, True), (0, True, False)]
)
def test_what_the_lab_failed_at_is_named(prepared: int | None, before: bool, after: bool) -> None:
    assert lab_failure(prepared, before, after) is not None


def test_a_prepared_run_with_serving_up_is_not_a_lab_failure() -> None:
    assert lab_failure(0, True, True) is None
