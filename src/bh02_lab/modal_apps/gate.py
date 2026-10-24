"""The gate: the one door the lab lead has. It runs a battery for a genome on the pinned model and
reports the score with its noise band; later phases add promotion here, behind the same door.

It is an HTTPS endpoint on Modal behind one bearer token (`GATE_TOKEN`), which can do nothing but
call it. A battery run is a job: `POST /batteries` starts it and returns its id, `GET
/batteries/{id}` reports it. The gate handles a genome only as plugin hashes; it never reads,
imports or runs a plugin.

Deploy: `modal deploy -m bh02_lab.modal_apps.gate`; the URL it prints is `BH02_LAB_GATE_URL`.
"""

import os
from typing import Any

import modal

from bh02_lab.battery import load_battery
from bh02_lab.contract import Rollout, Verdict
from bh02_lab.gate_core import BatteryAsk, requests_for, summary
from bh02_lab.modal_apps.common import APPS, BATTERY_JOBS, GATE_SECRET, LAB_ROOT, lab_image, pins, repo_root
from bh02_lab.pins import serving_hash

__all__ = ["app", "run_battery", "web"]

_PINS = pins()

app = modal.App(APPS["gate"])
jobs = modal.Dict.from_name(BATTERY_JOBS, create_if_missing=True)
image = lab_image("fastapi[standard]>=0.115").add_local_dir(
    repo_root() / "battery", str(LAB_ROOT / "battery"), ignore=["**/check/**", "**/reference/**", "**/repo/**"]
)


def _pins_now(battery: str) -> dict[str, str]:
    return {
        "image_digest": modal.Function.from_name(APPS["rollout"], "image_digest").remote(),
        "model": serving_hash(_PINS),
        "judge_sha": modal.Function.from_name(APPS["judge"], "pin").remote(battery),
        "bh02_sha": _PINS.bh02.sha,
        "jail": _PINS.rollout.jail,
    }


def _verdicts(rollouts: list[Rollout | BaseException], battery: str) -> list[Verdict | BaseException]:
    """Judge every rollout that ran, all at once; a rollout or a judgement the lab failed to run
    stays an exception in its place."""
    ran = [r for r in rollouts if not isinstance(r, BaseException)]
    verify = modal.Function.from_name(APPS["judge"], "verify")
    judged = iter(
        verify.map(
            [r.to_json() for r in ran], kwargs={"battery_name": battery},
            return_exceptions=True, wrap_returned_exceptions=False,
        )
    )  # fmt: skip
    found: list[Verdict | BaseException] = []
    for rollout in rollouts:
        if isinstance(rollout, BaseException):
            found.append(rollout)
            continue
        verdict = next(judged)
        found.append(verdict if isinstance(verdict, BaseException) else Verdict.from_json(verdict))
    return found


@app.function(image=image, timeout=4 * 60 * 60)
def run_battery(job_id: str, genome: list[str], k: int, battery: str) -> None:
    """Run every task of the battery k times on the genome, judge each, and keep the summary."""
    ask = BatteryAsk(frozenset(genome), k, battery)
    jobs[job_id] = {"status": "running", "ask": {"genome": sorted(genome), "k": k, "battery": battery}}
    try:
        pinned = _pins_now(battery)
        tasks = load_battery(battery, LAB_ROOT / "battery", with_checks=False)
        requests = requests_for(ask, list(tasks), pinned["image_digest"], pinned["model"])
        run = modal.Function.from_name(APPS["rollout"], "run")
        outcomes = run.map(
            [r.to_json() for r in requests], kwargs={"battery": battery},
            return_exceptions=True, wrap_returned_exceptions=False,
        )  # fmt: skip
        rollouts = [o if isinstance(o, BaseException) else Rollout.from_json(o) for o in outcomes]
        verdicts = _verdicts(rollouts, battery)
        jobs[job_id] = {"status": "done", **summary(ask, requests, verdicts, pinned)}
    except Exception as error:
        jobs[job_id] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        raise


@app.function(image=image, secrets=[modal.Secret.from_name(GATE_SECRET)], max_containers=2)
@modal.asgi_app(label="bh02-lab-gate")
def web() -> Any:
    """The gate's HTTPS API (`bh02_lab.gate_api`) over the job store and the battery runner."""
    from bh02_lab.gate_api import build_api

    def start(job_id: str, genome: list[str], k: int, battery: str) -> None:
        run_battery.spawn(job_id, genome, k, battery)

    store: Any = jobs  # a modal.Dict is a mapping by its methods, not by its type
    return build_api(store, start, _pins_now, os.environ["GATE_TOKEN"])
