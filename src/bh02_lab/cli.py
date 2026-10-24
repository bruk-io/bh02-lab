"""`bh02-lab`: the lab lead's one tool. It calls the gate over HTTPS and prints what the gate says
as JSON, so an agent reads it as data.

It needs two things in the environment and nothing else: `BH02_LAB_GATE_URL` (the gate's URL) and
`BH02_LAB_TOKEN` (the gate token, which can call the gate and nothing more). It holds no Modal or
Anthropic credential and never needs one.

`next_step` and `waited` are the decisions; the commands are the shell around them.
"""

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from enum import Enum, auto
from typing import Any, NoReturn

import click
import httpx

__all__ = ["Step", "main", "next_step", "waited"]

_RETRIES = 3


class Step(Enum):
    """What to do with a gate response."""

    DONE = auto()
    RETRY = auto()
    FAIL = auto()


def next_step(status: int, attempt: int) -> Step:
    """Retry a server error or a timeout a few times; anything else is the answer.

    >>> next_step(200, 1), next_step(503, 1), next_step(503, 3), next_step(401, 1)
    (<Step.DONE: 1>, <Step.RETRY: 2>, <Step.FAIL: 3>, <Step.FAIL: 3>)
    """
    if 200 <= status < 300:
        return Step.DONE
    if (status >= 500 or status == 0) and attempt < _RETRIES:
        return Step.RETRY
    return Step.FAIL


def waited(job: Mapping[str, Any]) -> bool:
    """Whether a battery run is still going.

    >>> waited({"status": "running"}), waited({"status": "done"})
    (True, False)
    """
    return job.get("status") in ("queued", "running")


def _fail(message: str) -> NoReturn:
    click.echo(f"error: {message}", err=True)
    sys.exit(1)


def _settings() -> tuple[str, str]:
    url, token = os.environ.get("BH02_LAB_GATE_URL", ""), os.environ.get("BH02_LAB_TOKEN", "")
    if not url or not token:
        _fail("set BH02_LAB_GATE_URL to the gate's URL and BH02_LAB_TOKEN to the gate token")
    return url.rstrip("/"), token


def _call(method: str, path: str, body: Mapping[str, Any] | None = None, sleep: Callable[[float], None] = time.sleep) -> Any:
    url, token = _settings()
    attempt = 1
    while True:
        try:
            response = httpx.request(
                method, f"{url}{path}", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=120.0
            )
            status, text = response.status_code, response.text
        except httpx.TransportError as error:
            status, text = 0, str(error)
        match next_step(status, attempt):
            case Step.DONE:
                return json.loads(text)
            case Step.RETRY:
                sleep(2.0**attempt)
                attempt += 1
            case Step.FAIL:
                _fail(f"the gate answered {status or 'nothing'} to {method} {path}: {text[:500]}")


def _print(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
def main() -> None:
    """Run the bh-02 lab through its gate. Every command prints JSON."""


@main.command("pins")
@click.option("--battery", default="core", show_default=True)
def pins_command(battery: str) -> None:
    """What a rollout is pinned to now: the image digest, the model, the judge and bh-02's SHA."""
    _print(_call("GET", f"/pins?battery={battery}"))


@main.group("battery")
def battery_group() -> None:
    """Score a genome on a battery."""


@battery_group.command("run")
@click.option("--genome", "genome", multiple=True, help="A plugin hash; repeat for each. None: the incumbent.")
@click.option("--k", default=5, show_default=True, help="Seeds per task.")
@click.option("--battery", default="core", show_default=True)
@click.option("--wait/--no-wait", default=True, show_default=True, help="Wait for the result, or print the run's id.")
@click.option("--poll-s", default=30.0, show_default=True)
@click.option("--timeout-s", default=4 * 60 * 60.0, show_default=True)
def battery_run(genome: tuple[str, ...], k: int, battery: str, wait: bool, poll_s: float, timeout_s: float) -> None:
    """Start a battery run; with --wait, print its summary when it ends (exit 1 if it failed)."""
    started = _call("POST", "/batteries", {"genome": list(genome), "k": k, "battery": battery})
    if not wait:
        _print(started)
        return
    deadline = time.monotonic() + timeout_s
    job = _call("GET", f"/batteries/{started['id']}")
    while waited(job):
        if time.monotonic() > deadline:
            _fail(f"battery run {started['id']} is still {job['status']}; `bh02-lab battery status {started['id']}` later")
        time.sleep(poll_s)
        job = _call("GET", f"/batteries/{started['id']}")
    _print(job)
    if job.get("status") != "done":
        sys.exit(1)


@battery_group.command("status")
@click.argument("run_id")
def battery_status(run_id: str) -> None:
    """A battery run's state, and its summary once it is done."""
    _print(_call("GET", f"/batteries/{run_id}"))
