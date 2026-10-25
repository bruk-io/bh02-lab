"""The judge: verifies a snapshot against a task's hidden check cases, and keeps its verdicts in a
store of its own.

It never imports bh-02 and its images hold none: a bh-02 change can't change how anything is
judged. The workspace's code runs only in the check sandbox (no network, no secret, no expected
value): the sandbox is handed the snapshot and each case's steps, runs them with
`bh02_lab/sandbox/cases.py`, and reports what each step did. This function, outside the sandbox,
compares that with the expected values (`battery.outcome`). A verdict is written once per
snapshot, task and judge pin, and never overwritten; asking again returns the first.

A failure of the lab's own (a snapshot not in the store, a sandbox that didn't start) raises, so
the gate counts it as the lab's error and no verdict is kept for it. Once the snapshot is in the
sandbox, its code may cause anything that goes wrong, so from then on a failure is a failing case.
Each case runs as `nobody` under resource limits, and the report goes to a file only root can
write.

Deploy: `modal deploy -m bh02_lab.modal_apps.judge`.
"""

import json
from pathlib import Path
from typing import Any

import modal

from bh02_lab.battery import judge_sha, load_battery, observed, outcome, public_steps
from bh02_lab.contract import Rollout, Verdict
from bh02_lab.hashing import tree_digest
from bh02_lab.modal_apps.common import APPS, ARTEFACTS, VERDICTS, lab_image, repo_root
from bh02_lab.snapshot import entries_of

__all__ = ["app", "pin", "verify"]

_HARNESS = "src/bh02_lab/sandbox/cases.py"
_PYTHON = "3.13"
_CASE_TIMEOUT_S = 20
_SANDBOX_TIMEOUT_S = 15 * 60
_JUDGE_SOURCES = (
    "src/bh02_lab/modal_apps/judge.py",
    "src/bh02_lab/battery.py",
    "src/bh02_lab/snapshot.py",
    "src/bh02_lab/hashing.py",
    _HARNESS,
)

app = modal.App(APPS["judge"])
artefacts = modal.Volume.from_name(ARTEFACTS, create_if_missing=True)
verdicts = modal.Dict.from_name(VERDICTS, create_if_missing=True)

judge_image = lab_image(ship={"battery": ["**/reference/**", "**/repo/**"], "src/bh02_lab": []})
check_image = (
    modal.Image.debian_slim(python_version=_PYTHON)
    .apt_install("util-linux")  # setpriv and prlimit: each case runs as nobody under limits
    .run_commands("mkdir -p /in /work /out && chmod 700 /in /out")
    .add_local_file(repo_root() / _HARNESS, "/opt/judge/cases.py", copy=True)
)


def _pin(battery_name: str) -> str:
    tasks = load_battery(battery_name, repo_root() / "battery")
    source = b"".join((repo_root() / path).read_bytes() for path in _JUDGE_SOURCES)
    return judge_sha(tasks.values(), source + f"check image: debian_slim python {_PYTHON}".encode())


@app.function(image=judge_image)
def pin(battery_name: str = "core") -> str:
    """The judge's own pin for a battery: its code, its check image and that battery's cases."""
    return _pin(battery_name)


@app.function(
    image=judge_image,
    volumes={"/artefacts": artefacts.with_mount_options(read_only=True)},
    timeout=_SANDBOX_TIMEOUT_S + 5 * 60,
    max_containers=32,
)
def verify(rollout_json: dict[str, Any], battery_name: str = "core") -> dict[str, Any]:
    """The verdict on a rollout's snapshot for its task, judged once.

    Raises:
        ValueError: The battery has no task by the rollout's task id, or the stored snapshot does
            not hash to its name.
        FileNotFoundError: The snapshot is not in the store (the lab's failure, not the model's).
    """
    rollout = Rollout.from_json(rollout_json)
    task_id = rollout.request.task_id
    tasks = load_battery(battery_name, repo_root() / "battery")
    if task_id not in tasks:
        raise ValueError(f"battery {battery_name!r} has no task {task_id!r}")
    judged_by = _pin(battery_name)
    key = Verdict(rollout.snapshot, task_id, False, judged_by).key
    if (kept := verdicts.get(key)) is not None:
        return dict(kept)
    artefacts.reload()  # a warm container sees snapshots committed since it started
    snapshot = Path("/artefacts/snapshots") / f"{rollout.snapshot}.tar"
    if not snapshot.exists():
        raise FileNotFoundError(f"snapshot {rollout.snapshot} is not in {ARTEFACTS}")
    tar = snapshot.read_bytes()
    if tree_digest(entries_of(tar)) != rollout.snapshot:
        raise ValueError(f"the stored snapshot {rollout.snapshot} does not hash to its name")
    task = tasks[task_id]
    if rollout.ended == "oversized":
        verdict = Verdict(rollout.snapshot, task_id, False, judged_by, "the workspace was over the size limit")
    else:
        found = outcome(task.cases, observed(_run_cases(tar, public_steps(task.cases))))
        verdict = Verdict(rollout.snapshot, task_id, found.passed, judged_by, found.detail)
    verdicts.put(key, verdict.to_json(), skip_if_exists=True)
    return dict(verdicts.get(key))


def _run_cases(tar: bytes, steps: list[list[dict[str, Any]]]) -> str:
    """Run the steps against the snapshot in a fresh sandbox with no network; the harness's report.

    Until the snapshot is in, a failure is the lab's and raises. From then on the workspace's code
    runs, and may be why anything fails, so a run that breaks or never finishes leaves what the
    report holds so far, and the cases it lacks fail.
    """
    request = {
        "work": "/work", "tar": "/in/snapshot.tar", "cases": steps, "timeout_s": _CASE_TIMEOUT_S,
        "report": "/out/report",
    }  # fmt: skip
    sandbox = modal.Sandbox.create(
        "sleep", "infinity", app=app, image=check_image, block_network=True,
        timeout=_SANDBOX_TIMEOUT_S, cpu=1.0, memory=(2048, 2048), workdir="/tmp",
    )  # fmt: skip
    try:
        sandbox.filesystem.write_bytes(tar, "/in/snapshot.tar")
        sandbox.filesystem.write_text(json.dumps(request), "/in/request.json")
        try:
            sandbox.exec(
                "python3", "-I", "/opt/judge/cases.py", "--request", "/in/request.json",
                stdout=modal.stream_type.StreamType.DEVNULL, stderr=modal.stream_type.StreamType.DEVNULL,
                timeout=_SANDBOX_TIMEOUT_S - 60,
            ).wait()  # fmt: skip
            return str(sandbox.filesystem.read_text("/out/report"))
        except Exception:  # the workspace's code may have caused it: what was reported stands
            try:
                return str(sandbox.filesystem.read_text("/out/report"))
            except Exception:  # no report at all: every case fails
                return ""
    finally:
        sandbox.terminate()
