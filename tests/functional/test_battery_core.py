"""The core battery is well formed: each task fails as shipped, passes with its reference, and is
judged by the same command the judge runs."""

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

from bh02_lab.battery import Task, check_command, judge_sha, load_battery, outcome, public_part
from bh02_lab.hashing import Entry

TASKS = load_battery("core")


def _write(root: Path, entries: Iterable[Entry]) -> None:
    for entry in entries:
        path = root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entry.content)


def _judged(tmp_path: Path, task: Task, work_files: Iterable[Entry]) -> bool:
    work, checks, report = tmp_path / "work", tmp_path / "checks", tmp_path / "report.xml"
    _write(work, work_files)
    _write(checks, task.check)
    subprocess.run(
        check_command(sys.executable, str(checks), str(work), str(report)),
        cwd=tmp_path,
        capture_output=True,
        timeout=120,
        check=False,
    )
    xml = report.read_text() if report.exists() else None
    return outcome(xml, task.checks).passed


def test_the_core_battery_has_six_tasks():
    assert len(TASKS) == 6


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_a_task_fails_as_shipped(tmp_path: Path, task_id: str):
    assert not _judged(tmp_path, TASKS[task_id], TASKS[task_id].repo)


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_a_task_passes_with_its_reference(tmp_path: Path, task_id: str):
    task = TASKS[task_id]
    solved = {e.path: e for e in task.repo} | {e.path: e for e in task.reference}
    assert _judged(tmp_path, task, solved.values())


def test_a_workspace_conftest_cannot_pass_a_task(tmp_path: Path):
    task = TASKS["paginate"]
    cheat = Entry("conftest.py", False, b"import pytest\ndef pytest_collection_modifyitems(items):\n    items.clear()\n")
    assert not _judged(tmp_path, task, [*task.repo, cheat])


def test_the_public_part_holds_no_check():
    for task in TASKS.values():
        part = public_part(task)
        assert set(part) == {"task_id", "prompt", "repo"}
        assert all(e.content.decode() not in repr(part) for e in task.check)


def test_the_judge_pin_moves_with_a_check_and_not_otherwise():
    tasks = list(TASKS.values())
    base = judge_sha(tasks, b"judge v1")
    assert base == judge_sha(tasks, b"judge v1")
    assert judge_sha(tasks, b"judge v2") != base
    first = tasks[0]
    edited = Task(first.task_id, first.title, first.checks, first.prompt, first.repo,
                  (Entry("test_x.py", False, b"assert True"),), first.reference)  # fmt: skip
    assert judge_sha([edited, *tasks[1:]], b"judge v1") != base


def test_a_copy_without_checks_loads_for_the_runner(tmp_path: Path):
    import shutil

    from bh02_lab.battery import BATTERIES

    shutil.copytree(BATTERIES / "core", tmp_path / "core", ignore=shutil.ignore_patterns("check", "reference"))
    tasks = load_battery("core", tmp_path, with_checks=False)
    assert set(tasks) == set(TASKS) and all(not task.check for task in tasks.values())
    with pytest.raises(Exception, match="check/"):
        load_battery("core", tmp_path)
