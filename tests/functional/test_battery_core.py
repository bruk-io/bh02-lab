"""The core battery is well formed: each task fails as shipped and passes with its reference,
judged the way the judge judges (the harness runs the steps, the comparison happens outside), and
a workspace can't talk its way to a pass."""

import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from bh02_lab.battery import BATTERIES, Task, judge_sha, load_battery, observed, outcome, public_part, public_steps
from bh02_lab.hashing import Entry
from bh02_lab.sandbox import cases

TASKS = load_battery("core")


def _write(root: Path, entries: Iterable[Entry]) -> None:
    for entry in entries:
        path = root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entry.content)


def _judged(tmp_path: Path, task: Task, work_files: Iterable[Entry]) -> bool:
    work = tmp_path / "work"
    _write(work, work_files)
    request = {"work": str(work), "cases": public_steps(task.cases), "timeout_s": 20}
    done = subprocess.run(
        [sys.executable, "-I", cases.__file__], input=json.dumps(request), capture_output=True, text=True, timeout=120,
        check=False,
    )  # fmt: skip
    return outcome(task.cases, observed(done.stdout)).passed


def test_the_core_battery_has_six_tasks() -> None:
    assert len(TASKS) == 6


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_a_task_fails_as_shipped(tmp_path: Path, task_id: str) -> None:
    assert not _judged(tmp_path, TASKS[task_id], TASKS[task_id].repo)


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_a_task_passes_with_its_reference(tmp_path: Path, task_id: str) -> None:
    task = TASKS[task_id]
    solved = {e.path: e for e in task.repo} | {e.path: e for e in task.reference}
    assert _judged(tmp_path, task, solved.values())


CHEATS = {
    "prints a forged report": b'print(\'CASE 0 [{"i": 0, "returned": [1, 2, 3]}]\')\n',
    "exits cleanly at import": b"import os\nos._exit(0)\n",
    "replaces json in the child": b'import json\njson.dumps = lambda *a, **k: \'{"i": 0, "returned": [1, 2, 3]}\'\n',
    "leaves a conftest and a sitecustomize": b"",
}


@pytest.mark.parametrize("cheat", sorted(CHEATS))
def test_a_workspace_cannot_talk_its_way_to_a_pass(tmp_path: Path, cheat: str) -> None:
    task = TASKS["paginate"]
    extra = [Entry("conftest.py", False, b"raise SystemExit"), Entry("sitecustomize.py", False, b"raise SystemExit")]
    files = [Entry("pager.py", False, CHEATS[cheat] or task.repo[0].content), *extra]
    assert not _judged(tmp_path, task, files)


def test_the_steps_the_sandbox_gets_hold_no_expected_value() -> None:
    for task in TASKS.values():
        assert "expect" not in json.dumps(public_steps(task.cases))


def test_the_public_part_holds_no_check() -> None:
    for task in TASKS.values():
        part = public_part(task)
        assert set(part) == {"task_id", "prompt", "repo"}
        assert all(e.content.decode() not in repr(part) for e in task.check)


def test_the_judge_pin_moves_with_a_check_and_not_otherwise() -> None:
    tasks = list(TASKS.values())
    base = judge_sha(tasks, b"judge v1")
    assert base == judge_sha(tasks, b"judge v1")
    assert judge_sha(tasks, b"judge v2") != base
    first = tasks[0]
    edited = Task(first.task_id, first.title, first.checks, first.prompt, first.repo,
                  (Entry("cases.json", False, b'{"cases": []}'),), first.reference)  # fmt: skip
    assert judge_sha([edited, *tasks[1:]], b"judge v1") != base


def test_a_copy_without_checks_loads_for_the_runner(tmp_path: Path) -> None:
    shutil.copytree(BATTERIES / "core", tmp_path / "core", ignore=shutil.ignore_patterns("check", "reference"))
    tasks = load_battery("core", tmp_path, with_checks=False)
    assert set(tasks) == set(TASKS) and all(not task.check for task in tasks.values())
    with pytest.raises(Exception, match="check/"):
        load_battery("core", tmp_path)
