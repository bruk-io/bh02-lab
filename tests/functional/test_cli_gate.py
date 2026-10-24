"""The CLI against the real gate API on a real socket, with the battery runner faked at the
boundary: what the lab lead sees, and what the gate refuses."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from click.testing import CliRunner

from bh02_lab.cli import main
from bh02_lab.gate_api import build_api

TOKEN = "gate-token-for-tests"


class FakeRunner:
    """Finishes every battery run at once, with a fixed summary, and keeps what it was asked."""

    def __init__(self, jobs: dict[str, Any]) -> None:
        self.jobs = jobs
        self.asked: list[tuple[str, list[str], int, str]] = []

    def __call__(self, job_id: str, genome: list[str], k: int, battery: str) -> None:
        self.asked.append((job_id, genome, k, battery))
        self.jobs[job_id] = {"status": "done", "score": 0.5, "band": [0.4, 0.6], "k": k}


@pytest.fixture
def gate() -> Iterator[tuple[str, FakeRunner]]:
    jobs: dict[str, Any] = {}
    runner = FakeRunner(jobs)
    api = build_api(jobs, runner, lambda battery: {"model": "srv-1", "battery": battery}, TOKEN)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}", runner
    server.should_exit = True
    thread.join(5)


def _cli(url: str, *args: str, token: str = TOKEN) -> Any:
    return CliRunner().invoke(main, list(args), env={"BH02_LAB_GATE_URL": url, "BH02_LAB_TOKEN": token})


def test_a_battery_run_waits_and_prints_the_summary(gate: tuple[str, FakeRunner]) -> None:
    url, runner = gate
    result = _cli(url, "battery", "run", "--k", "3", "--poll-s", "0")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["score"] == pytest.approx(0.5)
    assert runner.asked[0][1:] == ([], 3, "core")


def test_pins_come_back_as_json(gate: tuple[str, FakeRunner]) -> None:
    url, _ = gate
    result = _cli(url, "pins", "--battery", "core")
    assert json.loads(result.output) == {"model": "srv-1", "battery": "core"}


def test_a_wrong_token_is_refused_and_starts_nothing(gate: tuple[str, FakeRunner]) -> None:
    url, runner = gate
    result = _cli(url, "battery", "run", token="wrong")
    assert result.exit_code == 1 and "401" in result.output
    assert runner.asked == []


def test_every_route_needs_the_token(gate: tuple[str, FakeRunner]) -> None:
    url, _ = gate
    for method, path in (("GET", "/pins"), ("POST", "/batteries"), ("GET", "/batteries/x")):
        assert httpx.request(method, url + path, json={}).status_code == 401


def test_a_bad_ask_is_refused_with_why(gate: tuple[str, FakeRunner]) -> None:
    url, runner = gate
    result = _cli(url, "battery", "run", "--genome", "not-a-hash")
    assert result.exit_code == 1 and "422" in result.output and "plugin hashes" in result.output
    assert runner.asked == []


def test_the_cli_says_what_to_set_without_a_gate() -> None:
    result = CliRunner().invoke(main, ["pins"], env={"BH02_LAB_GATE_URL": "", "BH02_LAB_TOKEN": ""})
    assert result.exit_code == 1 and "BH02_LAB_GATE_URL" in result.output
