"""The judge: verifies a snapshot against a task's hidden checks in a fresh sandbox, and keeps its
verdicts in a store of its own.

It never imports bh-02 and its images hold none: a bh-02 change can't change how anything is
judged. The checks run in a sandbox built from the check image (Python, pytest and the checks),
with no network and no secret, against a copy of the snapshot the rollout runner stored. A verdict
is written once per snapshot, task and judge pin, and never overwritten; asking again returns the
first.

Deploy: `modal deploy -m bh02_lab.modal_apps.judge`.
"""

import secrets
from pathlib import Path
from typing import Any

import modal

from bh02_lab import battery
from bh02_lab.battery import Outcome, check_command, judge_sha, load_battery, outcome
from bh02_lab.contract import Rollout, Verdict
from bh02_lab.hashing import tree_digest
from bh02_lab.modal_apps.common import APPS, ARTEFACTS, LAB_ROOT, VERDICTS, lab_image, repo_root
from bh02_lab.snapshot import entries_of

__all__ = ["app", "pin", "verify"]

_CHECK_TIMEOUT_S = 300

app = modal.App(APPS["judge"])
artefacts = modal.Volume.from_name(ARTEFACTS, create_if_missing=True)
verdicts = modal.Dict.from_name(VERDICTS, create_if_missing=True)

judge_image = lab_image().add_local_dir(repo_root() / "battery", str(LAB_ROOT / "battery"), ignore=["**/reference/**"])
check_image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install("pytest==8.4.2")
    .add_local_dir(repo_root() / "battery", "/checks", copy=True, ignore=["**/repo/**", "**/reference/**"])
)


def _pin(name: str) -> str:
    tasks = load_battery(name, LAB_ROOT / "battery")
    source = Path(__file__).read_bytes() + Path(battery.__file__).read_bytes()
    return judge_sha(tasks.values(), source)


@app.function(image=judge_image)
def pin(battery_name: str = "core") -> str:
    """The judge's own pin for a battery: its code and that battery's checks."""
    return _pin(battery_name)


@app.function(image=judge_image, volumes={"/artefacts": artefacts.read_only()}, timeout=20 * 60, max_containers=32)
def verify(rollout_json: dict[str, Any], battery_name: str = "core") -> dict[str, Any]:
    """The verdict on a rollout's snapshot for its task, judged once.

    Raises:
        ValueError: The battery has no task by the rollout's task id.
    """
    rollout = Rollout.from_json(rollout_json)
    task_id = rollout.request.task_id
    tasks = load_battery(battery_name, LAB_ROOT / "battery")
    if task_id not in tasks:
        raise ValueError(f"battery {battery_name!r} has no task {task_id!r}")
    judged_by = _pin(battery_name)
    key = Verdict(rollout.snapshot, task_id, False, judged_by).key
    if (kept := verdicts.get(key)) is not None:
        return dict(kept)
    snapshot = Path("/artefacts/snapshots") / f"{rollout.snapshot}.tar"
    tar = snapshot.read_bytes() if snapshot.exists() else None
    if rollout.ended.startswith("unreadable") or tar is None:
        verdict = Verdict(rollout.snapshot, task_id, False, judged_by, f"no snapshot to judge ({rollout.ended})")
    elif tree_digest(entries_of(tar)) != rollout.snapshot:
        verdict = Verdict(rollout.snapshot, task_id, False, judged_by, "the stored snapshot does not hash to its name")
    else:
        found = _checked(tar, battery_name, task_id, tasks[task_id].checks)
        verdict = Verdict(rollout.snapshot, task_id, found.passed, judged_by, found.detail)
    verdicts.put(key, verdict.to_json(), skip_if_exists=True)
    return dict(verdicts.get(key))


def _checked(tar: bytes, battery_name: str, task_id: str, expected: int) -> Outcome:
    """Run the task's checks against the snapshot in a fresh sandbox with no network."""
    report = f"/out/{secrets.token_hex(16)}.xml"  # a name the workspace can't know in advance
    sandbox = modal.Sandbox.create(
        "sleep", "infinity", app=app, image=check_image, block_network=True,
        timeout=_CHECK_TIMEOUT_S + 120, cpu=1.0, memory=2048, workdir="/tmp",
    )  # fmt: skip
    try:
        files = sandbox.filesystem
        files.make_directory("/work", create_parents=True)
        files.make_directory("/out", create_parents=True)
        for entry in entries_of(tar):
            files.make_directory(str(Path("/work", entry.path).parent), create_parents=True)
            files.write_bytes(entry.content, f"/work/{entry.path}")
        checks = f"/checks/{battery_name}/{task_id}/check"
        sandbox.exec(*check_command("python", checks, "/work", report), timeout=_CHECK_TIMEOUT_S).wait()
        try:
            xml: str | None = files.read_text(report)
        except Exception:  # no report is a verdict of its own, whatever stopped it
            xml = None
    finally:
        sandbox.terminate()
    return outcome(xml, expected)
