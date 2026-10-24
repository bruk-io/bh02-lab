"""A battery: the tasks a genome is scored on, and how a check run becomes a verdict.

A task is a directory: `task.toml` (its title and how many hidden checks it has), `prompt.md` (the
one message bh-02 is sent), `repo/` (the workspace it starts from), `check/` (the hidden checks,
which only the judge ever holds) and `reference/` (files that solve it, for the battery's own
tests). The rollout side gets `prompt` and `repo` and nothing else (`public_part`).

A check run passes only when the run reports exactly the task's number of checks, all passed,
none skipped. A workspace that changes how many checks run, or that stops pytest before it
reports, fails.

`load_battery` reads the directory; the rest is pure.
"""

import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bh02_lab.hashing import Entry, tree_digest
from bh02_lab.snapshot import read_tree

__all__ = ["BatteryError", "Outcome", "Task", "check_command", "judge_sha", "load_battery", "outcome", "public_part"]

BATTERIES = Path(__file__).resolve().parents[2] / "battery"


class BatteryError(ValueError):
    """A battery directory that is not the shape a battery is."""


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


@dataclass(frozen=True, slots=True)
class Outcome:
    """Whether a check run passed, and a line saying why."""

    passed: bool
    detail: str


def public_part(task: Task) -> dict[str, object]:
    """What a rollout may see of a task: the prompt and the starting files, never the checks."""
    return {"task_id": task.task_id, "prompt": task.prompt, "repo": [(e.path, e.executable) for e in task.repo]}


def outcome(junit_xml: str | None, expected: int) -> Outcome:
    """A check run's verdict from pytest's JUnit report.

    >>> outcome(None, 3)
    Outcome(passed=False, detail='no report: the checks did not finish')
    >>> report = '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    >>> outcome(report, 3)
    Outcome(passed=True, detail='3/3 checks passed')
    >>> outcome(report, 4).passed
    False
    """
    if junit_xml is None:
        return Outcome(False, "no report: the checks did not finish")
    try:
        root = ElementTree.fromstring(junit_xml)
    except ElementTree.ParseError as error:
        return Outcome(False, f"the report can't be read: {error}")
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return Outcome(False, "the report holds no test suite")
    count = {name: sum(int(suite.get(name, "0")) for suite in suites) for name in ("tests", "failures", "errors", "skipped")}
    passed = count["tests"] - count["failures"] - count["errors"] - count["skipped"]
    if count["tests"] != expected:
        return Outcome(False, f"{count['tests']} checks ran, the task has {expected}")
    if passed != expected:
        return Outcome(
            False,
            f"{passed}/{expected} checks passed "
            f"({count['failures']} failed, {count['errors']} errors, {count['skipped']} skipped)",
        )
    return Outcome(True, f"{passed}/{expected} checks passed")


def check_command(python: str, checks: str, work: str, report: str) -> list[str]:
    """How the judge runs a task's hidden checks against a workspace, the one definition (the
    battery's own tests run it too). The workspace is on the import path but is not where pytest
    looks for configuration or conftest files, so a file the model left can't reshape the run.

    >>> check_command("python", "/checks/t", "/work", "/out/r.xml")[:3]
    ['python', '-m', 'pytest']
    """
    return [
        python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest",
        "--rootdir", checks, "-c", "/dev/null", "-o", f"pythonpath={work}",
        f"--junitxml={report}", checks,
    ]  # fmt: skip


def judge_sha(tasks: Iterable[Task], judge_source: bytes) -> str:
    """The judge's own pin: its code and every task's checks and expected count. bh-02's SHA is
    not in it, so a bh-02 change never changes how a snapshot is judged."""
    entries = [Entry("judge.py", False, judge_source)]
    for task in tasks:
        entries.append(Entry(f"{task.task_id}/checks", False, str(task.checks).encode()))
        entries.extend(Entry(f"{task.task_id}/check/{e.path}", e.executable, e.content) for e in task.check)
    return "judge-" + tree_digest(entries)[:16]


def load_battery(name: str, root: Path = BATTERIES) -> Mapping[str, Task]:
    """The tasks of battery `name`, by id.

    Raises:
        BatteryError: The battery has no tasks, or a task lacks a part or has a bad `task.toml`.
    """
    directory = root / name
    tasks = {path.name: _task(path) for path in sorted(directory.iterdir()) if path.is_dir()} if directory.is_dir() else {}
    if not tasks:
        raise BatteryError(f"battery {name!r} has no tasks under {directory}")
    return tasks


def _task(path: Path) -> Task:
    try:
        meta = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
        prompt = (path / "prompt.md").read_text(encoding="utf-8")
        title, checks = str(meta["title"]), int(meta["checks"])
    except (OSError, KeyError, ValueError, TypeError) as error:
        raise BatteryError(f"task {path.name}: {type(error).__name__}: {error}") from error
    parts = {part: tuple(read_tree(path / part)) if (path / part).is_dir() else () for part in ("repo", "check", "reference")}
    if not parts["repo"] or not parts["check"]:
        raise BatteryError(f"task {path.name} needs a repo/ and a check/ directory, each with files")
    return Task(path.name, title, checks, prompt, parts["repo"], parts["check"], parts["reference"])
