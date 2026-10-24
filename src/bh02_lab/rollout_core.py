"""What a finished rollout was, decided on the trusted side from what the sandbox left.

The sandbox's program runs in two commands. `prepare` sets bh-02 up before any of the model's code
exists, so its failure is the lab's; so is a serving endpoint that is down before the run or after
it (the run would have failed for it). Those are `LabError`s, which the gate leaves out of the
score. From the moment `run` starts bh-02, the model's code can cause anything that goes wrong, so
everything from there is the model's: a missing or unreadable result is `crashed`, a workspace
over the caps is `oversized`, and an ending is taken from `result.json` only when it is a known
one.

Pure.
"""

import json

from bh02_lab.hashing import Entry
from bh02_lab.snapshot import SnapshotError, entries_of

__all__ = ["ENDINGS", "LabError", "lab_failure", "settled"]

ENDINGS = frozenset({"replied", "budget", "crashed", "oversized"})


class LabError(RuntimeError):
    """The lab failed to run the rollout; nothing about the model follows from it."""


def lab_failure(prepared: int | None, serving_before: bool, serving_after: bool) -> str | None:
    """Why the lab, not the model, failed this rollout, or None when it didn't.

    >>> lab_failure(0, True, True) is None
    True
    >>> lab_failure(1, True, True)
    'the sandbox could not be prepared (exit 1)'
    """
    if prepared != 0:
        return f"the sandbox could not be prepared (exit {prepared})"
    if not serving_before:
        return "the serving endpoint was down before the run"
    if not serving_after:
        return "the serving endpoint was down after the run"
    return None


def settled(result: bytes | None, tar: bytes | None, tar_over: bool) -> tuple[list[Entry], str]:
    """The snapshot's entries and how the rollout ended, once bh-02 has been started. Never the
    lab's failure: whatever is missing, the model's code may have caused it.

    >>> settled(None, None, False)
    ([], 'crashed')
    """
    try:
        said = json.loads(result).get("ended") if result is not None else None
    except (ValueError, AttributeError):
        said = None
    ended = said if said in ENDINGS else "crashed"
    if ended == "oversized" or tar_over:
        return [], "oversized"
    if tar is None:
        return [], "crashed"
    try:
        return entries_of(tar), ended
    except SnapshotError:
        return [], "oversized"
