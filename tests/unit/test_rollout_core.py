import pytest

from bh02_lab.hashing import Entry
from bh02_lab.rollout_core import LabError, settled
from bh02_lab.snapshot import packed

TAR = packed([Entry("a.py", False, b"x = 1\n")])


def test_a_clean_run_keeps_its_ending_and_files() -> None:
    entries, ended = settled(0, b'{"ended": "budget"}', TAR)
    assert ended == "budget" and [e.path for e in entries] == ["a.py"]


@pytest.mark.parametrize("result", [b'{"ended": "passed all tests!"}', b"not json", b"[]", b'{"ended": 3}'])
def test_an_ending_it_does_not_know_is_a_crash(result: bytes) -> None:
    assert settled(0, result, TAR)[1] == "crashed"


def test_an_unusable_workspace_is_oversized_not_a_lab_error() -> None:
    assert settled(0, b'{"ended": "replied"}', b"garbage" * 100) == ([], "oversized")


@pytest.mark.parametrize(
    ("code", "result", "tar"), [(1, b"{}", TAR), (None, b"{}", TAR), (0, None, TAR), (0, b"{}", None)]
)
def test_a_sandbox_that_did_not_finish_is_the_labs_failure(
    code: int | None, result: bytes | None, tar: bytes | None
) -> None:
    with pytest.raises(LabError):
        settled(code, result, tar)
