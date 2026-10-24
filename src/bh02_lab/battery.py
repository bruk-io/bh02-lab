"""A battery: the tasks a genome is scored on, and how what the judge observed becomes a verdict.

A task is a directory: `task.toml` (its title and how many check cases it has), `prompt.md` (the
one message bh-02 is sent), `repo/` (the workspace it starts from), `check/cases.json` (the hidden
check cases: steps to run against the workspace, and the value each step must give) and
`reference/` (files that solve it, for the battery's own tests). The rollout side gets `prompt` and
`repo` and nothing else (`public_part`).

Checks are data, not test code, so the comparison never runs where the workspace's code does:
the judge's sandbox is handed each case's steps without their expected values
(`public_steps`), runs them with `bh02_lab/sandbox/cases.py`, and reports what each step did. The
judge compares that with the expected values here (`outcome`). Code in the workspace can make a
step return or print anything, but it passes only by giving the expected answers.

A task passes when every one of its cases passes: every step with an expected value was observed
and matches. Anything missing is a failure.

`load_battery` and `task_ids` read the directory; the rest is pure.
"""

import json
import math
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bh02_lab.hashing import Entry, tree_digest
from bh02_lab.snapshot import read_tree

__all__ = [
    "BatteryError",
    "Case",
    "Outcome",
    "Task",
    "judge_sha",
    "load_battery",
    "observed",
    "outcome",
    "public_part",
    "public_steps",
    "task_ids",
]

BATTERIES = Path(__file__).resolve().parents[2] / "battery"
CASES = "cases.json"
_EXPECTATIONS = frozenset({"returns", "approx", "raises", "stdout", "stdout_json"})
_TOLERANCE = 1e-6


class BatteryError(ValueError):
    """A battery directory that is not the shape a battery is."""


@dataclass(frozen=True, slots=True)
class Case:
    """One check case: a name and its steps, each step maybe with an `expect`."""

    name: str
    steps: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class Task:
    """One task: what the model is told and starts from, and what the judge checks."""

    task_id: str
    title: str
    checks: int
    prompt: str
    repo: tuple[Entry, ...]
    check: tuple[Entry, ...]
    reference: tuple[Entry, ...]

    @property
    def cases(self) -> tuple[Case, ...]:
        """The task's check cases, or none in a copy without checks.

        Raises:
            BatteryError: `check/cases.json` is not the shape cases are.
        """  # ruff: ignore[docstring-extraneous-exception] - _cases raises it
        found = [entry for entry in self.check if entry.path == CASES]
        if not found:
            return ()
        return _cases(self.task_id, found[0].content)


@dataclass(frozen=True, slots=True)
class Outcome:
    """Whether a task's cases passed, and a line saying why."""

    passed: bool
    detail: str


def public_part(task: Task) -> dict[str, object]:
    """What a rollout may see of a task: the prompt and the starting files, never the checks."""
    return {"task_id": task.task_id, "prompt": task.prompt, "repo": [(e.path, e.executable) for e in task.repo]}


def public_steps(cases: Sequence[Case]) -> list[list[dict[str, Any]]]:
    """The cases as the judge's sandbox gets them: every step without its expected value."""
    return [[{k: v for k, v in step.items() if k != "expect"} for step in case.steps] for case in cases]


def observed(harness_output: str) -> dict[int, list[Mapping[str, Any]]]:
    """The harness's report, by case number; a line that is not a case's report is ignored, and
    so is a second report of a case.

    >>> observed('noise\\nCASE 0 [{"i": 0, "returned": 3}]\\nCASE 1 not json')
    {0: [{'i': 0, 'returned': 3}]}
    """
    found: dict[int, list[Mapping[str, Any]]] = {}
    for line in harness_output.splitlines():
        head, _, rest = line.partition(" ")
        number, _, data = rest.partition(" ")
        if head != "CASE" or not number.isdigit() or int(number) in found:
            continue
        try:
            records = json.loads(data)
        except ValueError:
            continue
        if isinstance(records, list):
            found[int(number)] = [r for r in records if isinstance(r, dict)]
    return found


def outcome(cases: Sequence[Case], observations: Mapping[int, Sequence[Mapping[str, Any]]]) -> Outcome:
    """The verdict: every case passes, or the first one that didn't and where."""
    if not cases:
        return Outcome(False, "the task has no check cases")
    for number, case in enumerate(cases):
        seen = {record.get("i"): record for record in observations.get(number, ())}
        for i, step in enumerate(case.steps):
            if "expect" not in step:
                continue
            record = seen.get(i)
            if record is None:
                return Outcome(False, f"case {case.name!r}: step {i} was not observed")
            if not _matches(step["expect"], record):
                return Outcome(False, f"case {case.name!r}: step {i} did not give the expected result")
    return Outcome(True, f"{len(cases)}/{len(cases)} cases passed")


_MISSING = object()


def _matches(expect: Mapping[str, Any], record: Mapping[str, Any]) -> bool:
    raised = "raised" in record
    returned = record.get("returned", _MISSING)
    for kind, want in expect.items():
        match kind:
            case "returns":
                ok = not raised and returned == want
            case "approx":
                ok = not raised and _close(returned, want)
            case "raises":
                ok = want in record.get("raised", ())
            case "stdout":
                ok = record.get("stdout") == want
            case "stdout_json":
                ok = _json(record.get("stdout")) == want
            case _:
                ok = False
        if not ok:
            return False
    return True


def _close(seen: Any, want: Any) -> bool:
    if isinstance(want, bool) or isinstance(seen, bool):
        return seen is want
    if isinstance(want, int | float):
        return isinstance(seen, int | float) and math.isclose(seen, want, rel_tol=_TOLERANCE, abs_tol=_TOLERANCE)
    if isinstance(want, dict):
        return isinstance(seen, dict) and seen.keys() == want.keys() and all(_close(seen[k], want[k]) for k in want)
    if isinstance(want, list):
        return isinstance(seen, list) and len(seen) == len(want) and all(map(_close, seen, want))
    return bool(seen == want)


def _json(text: Any) -> Any:
    if not isinstance(text, str):
        return _MISSING
    try:
        return json.loads(text)
    except ValueError:
        return _MISSING


def _cases(task_id: str, content: bytes) -> tuple[Case, ...]:
    try:
        data = json.loads(content)
        cases = tuple(Case(str(c["name"]), tuple(c["steps"])) for c in data["cases"])
    except (ValueError, KeyError, TypeError) as error:
        raise BatteryError(f"task {task_id}: check/{CASES}: {type(error).__name__}: {error}") from error
    for case in cases:
        for step in case.steps:
            unknown = set(step.get("expect", {})) - _EXPECTATIONS
            if unknown:
                known = sorted(_EXPECTATIONS)
                raise BatteryError(f"task {task_id}: case {case.name!r} expects {sorted(unknown)}; known: {known}")
    return cases


def judge_sha(tasks: Iterable[Task], judge_source: bytes) -> str:
    """The judge's own pin: its code and image (as given) and every task's cases. bh-02's SHA is
    not in it, so a bh-02 change never changes how a snapshot is judged."""
    entries = [Entry("judge", False, judge_source)]
    for task in tasks:
        entries.append(Entry(f"{task.task_id}/checks", False, str(task.checks).encode()))
        entries.extend(Entry(f"{task.task_id}/check/{e.path}", e.executable, e.content) for e in task.check)
    return "judge-" + tree_digest(entries)[:16]


def task_ids(name: str, root: Path = BATTERIES) -> list[str]:
    """The ids of battery `name`'s tasks, from their `task.toml` alone.

    Raises:
        BatteryError: The battery has no tasks.
    """
    directory = root / name
    found = sorted(p.parent.name for p in directory.glob("*/task.toml")) if directory.is_dir() else []
    if not found:
        raise BatteryError(f"battery {name!r} has no tasks under {directory}")
    return found


def load_battery(name: str, root: Path = BATTERIES, *, with_checks: bool = True) -> Mapping[str, Task]:
    """The tasks of battery `name`, by id. `with_checks=False` reads a copy that holds no checks
    (the rollout runner's), where each task's `check` is empty.

    Raises:
        BatteryError: The battery has no tasks, a task lacks a part or has a bad `task.toml`, or
            its number of cases is not the number `task.toml` says.
    """  # ruff: ignore[docstring-extraneous-exception] - task_ids and _task raise it
    return {task_id: _task(root / name / task_id, with_checks) for task_id in task_ids(name, root)}


def _task(path: Path, with_checks: bool) -> Task:
    try:
        meta = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
        prompt = (path / "prompt.md").read_text(encoding="utf-8")
        title, checks = str(meta["title"]), int(meta["checks"])
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise BatteryError(f"task {path.name}: {type(error).__name__}: {error}") from error
    parts = {
        part: tuple(read_tree(path / part)) if (path / part).is_dir() else () for part in ("repo", "check", "reference")
    }
    if not parts["repo"] or (with_checks and not parts["check"]):
        raise BatteryError(f"task {path.name} needs a repo/ and a check/ directory, each with files")
    task = Task(path.name, title, checks, prompt, parts["repo"], parts["check"], parts["reference"])
    if with_checks and len(task.cases) != checks:
        raise BatteryError(f"task {path.name}: task.toml says {checks} cases, check/{CASES} has {len(task.cases)}")
    return task
