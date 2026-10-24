"""What a finished rollout was, decided on the trusted side from what the sandbox left.

The sandbox's program exits 0 and writes `result.json` last, after everything else; a sandbox
that didn't get that far is the lab's failure (`LabError`), which the gate leaves out of the score
rather than counting against the model. Everything else is the model's: how it ended is taken
from `result.json` only when it is one of the known endings, and a workspace too big to be a
snapshot is `oversized`, a failure the judge records as one.

Pure.
"""

import json

from bh02_lab.hashing import Entry
from bh02_lab.snapshot import SnapshotError, entries_of

__all__ = ["ENDINGS", "LabError", "settled"]

ENDINGS = frozenset({"replied", "budget", "crashed"})


class LabError(RuntimeError):
    """The lab failed to run the rollout; nothing about the model follows from it."""


def settled(exit_code: int | None, result: bytes | None, tar: bytes | None) -> tuple[list[Entry], str]:
    """The snapshot's entries and how the rollout ended.

    >>> settled(0, b'{"ended": "replied"}', None)
    Traceback (most recent call last):
    ...
    bh02_lab.rollout_core.LabError: the sandbox left no workspace

    Raises:
        LabError: The sandbox's program failed or left no result or no workspace.
    """
    if exit_code != 0:
        raise LabError(f"the sandbox's program exited {exit_code}")
    if result is None:
        raise LabError("the sandbox left no result")
    if tar is None:
        raise LabError("the sandbox left no workspace")
    try:
        said = json.loads(result).get("ended")
    except (ValueError, AttributeError):
        said = None
    ended = said if said in ENDINGS else "crashed"
    try:
        return entries_of(tar), ended
    except SnapshotError:
        return [], "oversized"
