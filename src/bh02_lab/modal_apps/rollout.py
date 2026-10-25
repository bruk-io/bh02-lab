"""Rollouts: bh-02 at its pinned SHA, headless, in a single-use Modal Sandbox per request.

Two images. The rollout image is what runs untrusted: bh-02 at the pinned SHA with every plugin,
the headless row, and the sandbox's program; no lab code, no checks, no credential but the
inference key, which arrives as a secret. The runner function is trusted lab code: it starts the
sandbox, hands it the request and the task's public part, and takes back the workspace, the
transcript and the event log, which it hashes and stores itself.

The image is pinned by the id Modal gives the built image (its digest here); a request for any
other digest is refused, so a rollout always ran on the image it names.

Deploy: `modal deploy -m bh02_lab.modal_apps.rollout`.
Spike (TASK-0001.04): `modal run -m bh02_lab.modal_apps.rollout::probe`.
"""

import functools
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import modal

from bh02_lab.battery import load_battery
from bh02_lab.contract import Rollout, RolloutRequest
from bh02_lab.hashing import digest, tree_digest
from bh02_lab.modal_apps.common import APPS, ARTEFACTS, INFERENCE_SECRET, lab_image, pins, repo_root
from bh02_lab.pins import Pins, serving_hash
from bh02_lab.rollout_core import LabError, lab_failure, settled
from bh02_lab.snapshot import MAX_BYTES, packed

__all__ = ["app", "image_digest", "probe_jail", "run", "sandbox_request"]

_PINS = pins()
_HEADLESS = "/opt/headless"
_ENTRY = "/opt/lab/entry.py"
_ENTRY_SOURCE = "src/bh02_lab/sandbox/entry.py"
_LEFT_OUT = ["tests", "tests/**", "**/__pycache__/**", "**/.pytest_cache/**", "**/*.egg-info/**"]
_SANDBOX_GRACE_S = 600
_AGENT_UID = 1000  # who bh-02 runs as when it has no jail of its own
_RUNTIMES: dict[str, Literal["gvisor", "vm"]] = {"gvisor": "gvisor", "vm": "vm"}
_OUTPUTS = ("result.json", "work.tar", "transcript.jsonl", "events.jsonl", "bh02.log")

app = modal.App(APPS["rollout"])
artefacts = modal.Volume.from_name(ARTEFACTS, create_if_missing=True)


def _rollout_image(pinned: Pins) -> modal.Image:
    bh02 = pinned.bh02
    return (
        modal.Image.debian_slim(python_version="3.13")
        # bh-02 runs on Python 3.15, which several of its dependencies publish no wheels for yet
        # (pydantic-core, tree-sitter, pyyaml): uv builds them from source, so the image needs a C
        # compiler and a current Rust toolchain (Debian's rustc is too old for pydantic-core).
        .apt_install("git", "bubblewrap", "ca-certificates", "curl", "build-essential", "pkg-config")
        .run_commands("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal")
        .env(
            {
                "PATH": "/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                # outside /root, so bh-02's interpreter runs for the agent user too (jail = "sandbox")
                "UV_PYTHON_INSTALL_DIR": "/opt/uv-python",
            }
        )
        .pip_install(f"uv=={bh02.uv}")  # the base image's own uv is too old to install Python 3.15
        .run_commands(
            f"git clone {bh02.repo} /opt/bh-02",
            f"git -C /opt/bh-02 checkout --detach {bh02.sha}",
            f"cd /opt/bh-02 && uv python install {bh02.python}",
            "cd /opt/bh-02 && uv sync --all-packages --frozen --no-dev",
        )
        .add_local_dir(repo_root() / "headless", _HEADLESS, copy=True, ignore=_LEFT_OUT)
        .run_commands(f"uv pip install --python /opt/bh-02/.venv/bin/python --no-deps {_HEADLESS}")
        .add_local_file(repo_root() / _ENTRY_SOURCE, _ENTRY, copy=True)
        .run_commands(
            f"mkdir -p /work /lab && useradd --uid {_AGENT_UID} --no-create-home --shell /usr/sbin/nologin agent"
        )
    )


# The rollout image is defined from repository files; the runner carries them, so the image defined
# again in its containers (where the sandboxes start) reads the same files.
runner_image = lab_image(ship={"battery": ["**/check/**", "**/reference/**"], "headless": _LEFT_OUT, _ENTRY_SOURCE: []})
rollout_image = _rollout_image(_PINS)


def sandbox_request(
    request: RolloutRequest, prompt: str, base_url: str, plugins: list[dict[str, str]], pinned: Pins
) -> dict[str, Any]:
    """What the sandbox's program is handed: the task's prompt, the endpoint, the budget, the jail
    mode and the genome's plugin sources. Never a check, never a credential."""
    return {
        "prompt": prompt,
        "provider": "openai",
        "base_url": base_url,
        "model": pinned.model.served_name,
        "temperature": pinned.model.temperature,
        "budget_s": pinned.rollout.budget_s,
        "jail": pinned.rollout.jail,
        "plugins": plugins,
        "task_id": request.task_id,
        "seed": request.seed,
    }


def _digest() -> str:
    return rollout_image.build(app).object_id


def _base_url() -> str:
    url = modal.Function.from_name(APPS["serve"], "serve").get_web_url()
    if not url:
        raise RuntimeError(f"{APPS['serve']} is not deployed; run `modal deploy -m bh02_lab.modal_apps.serve` first")
    return url.rstrip("/") + "/v1"


def _plugins(genome: frozenset[str]) -> list[dict[str, str]]:
    """The genome's plugin sources from the volume, each checked against the hash that names it.

    Raises:
        ValueError: A stored plugin does not hash to its name.
    """
    found = []
    if genome:
        artefacts.reload()  # a warm container sees plugins stored since it started
    for plugin_hash in sorted(genome):
        source = Path(f"/artefacts/genome/{plugin_hash}.py").read_bytes()
        if digest(source) != plugin_hash:
            raise ValueError(
                f"genome plugin {plugin_hash} does not hash to its name; it was changed after it was stored"
            )
        found.append({"name": f"g_{plugin_hash[:12]}", "source": source.decode()})
    return found


def _serving(base_url: str, key: str) -> bool:
    """Whether the serving endpoint answers with the key (it may be scaling up: give it time)."""
    import httpx

    try:
        answer = httpx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20 * 60.0)
    except httpx.HTTPError:
        return False
    return answer.status_code == 200


def _sandbox_run(
    payload: dict[str, Any],
    repo: list[tuple[str, bytes, bool]],
    allow: list[str] | None,
    key: str,
    serving: Callable[[], bool],
    runtime: str = _PINS.rollout.runtime,
) -> tuple[str | None, dict[str, bytes | None], bool]:
    """Start a fresh sandbox, prepare it, check serving, run bh-02, check serving again, read the
    outputs back, and end it. Returns why the lab failed (None if it didn't), each output (None
    when it isn't there) and whether the workspace tar was over the cap. `allow`: the only domains
    the sandbox may reach (None: no limit, for the probe alone). The key goes in as a file the
    program removes, never as a secret or an environment variable of the sandbox."""
    budget = int(payload["budget_s"])
    sandbox = modal.Sandbox.create(
        "sleep", "infinity", app=app, image=rollout_image, timeout=budget + _SANDBOX_GRACE_S,
        cpu=2.0, memory=(4096, 8192), workdir="/work", outbound_domain_allowlist=allow, runtime=_RUNTIMES[runtime],
    )  # fmt: skip
    try:
        files = sandbox.filesystem
        for path, content, executable in repo:
            files.make_directory(str(Path(f"/work/{path}").parent), create_parents=True)
            files.write_bytes(content, f"/work/{path}")
            if executable:
                sandbox.exec("chmod", "+x", f"/work/{path}").wait()
        files.make_directory("/lab", create_parents=True)
        files.write_text(json.dumps(payload), "/lab/request.json")
        files.write_text(key, "/lab/key")
        prepare = sandbox.exec(
            "python3", _ENTRY, "prepare", "--key-file", "/lab/key", "--agent-uid", str(_AGENT_UID), timeout=600
        )
        prepare.wait()
        before = serving()
        failed = lab_failure(prepare.returncode, before, True)
        if failed is not None:
            told = str(prepare.stderr.read())[-600:]
            try:
                smoke = files.read_bytes("/lab/out/smoke.log")
            except Exception:  # no smoke log: the failure came before the smoke run
                smoke = b""
            return f"{failed}: {told}", {"smoke.log": smoke}, False
        # From here the model's code exists: whatever goes wrong may be its doing.
        sandbox.exec(
            "python3", _ENTRY, "run", "--agent-uid", str(_AGENT_UID), timeout=budget + _SANDBOX_GRACE_S - 60
        ).wait()
        failed = lab_failure(0, True, serving())
        outputs: dict[str, bytes | None] = {}
        tar_over = False
        for name in (*_OUTPUTS, "smoke.log"):
            try:
                if name == "work.tar" and files.stat(f"/lab/out/{name}").size > int(MAX_BYTES * 1.25):
                    tar_over, outputs[name] = True, None
                    continue
                outputs[name] = files.read_bytes(f"/lab/out/{name}")
            except Exception:  # an output that isn't there; `settled` decides what that means
                outputs[name] = None
        return failed, outputs, tar_over
    finally:
        sandbox.terminate()


@app.function(
    image=runner_image,
    volumes={"/artefacts": artefacts},
    secrets=[modal.Secret.from_name(INFERENCE_SECRET)],
    timeout=2 * 60 * 60,
    max_containers=32,
)
def run(request_json: dict[str, Any], battery: str = "core") -> dict[str, Any]:
    """Run one rollout of a task of `battery` and store what it left; returns the `Rollout` as JSON.

    Raises:
        ValueError: The request names another image or model than the pinned ones, or a task the
            battery does not have.
        LabError: The sandbox's program did not finish (the lab's failure, not the model's).
    """  # ruff: ignore[docstring-extraneous-exception] - _stored raises it
    import os

    request = RolloutRequest.from_json(request_json)
    built = _digest()
    if request.image_digest != built:
        raise ValueError(f"the request names image {request.image_digest}, but the pinned image is {built}")
    if request.model != serving_hash(_PINS):
        raise ValueError(f"the request names model {request.model}, but the pinned model is {serving_hash(_PINS)}")
    tasks = load_battery(battery, repo_root() / "battery", with_checks=False)
    task = tasks.get(request.task_id)
    if task is None:
        raise ValueError(f"battery {battery!r} has no task {request.task_id!r}")
    base_url = _base_url()
    payload = sandbox_request(request, task.prompt, base_url, _plugins(request.genome), _PINS)
    repo = [(e.path, e.content, e.executable) for e in task.repo]
    key = os.environ["ORNITH_KEY"]
    failed, outputs, tar_over = _sandbox_run(
        payload, repo, [urlparse(base_url).hostname or ""], key, functools.partial(_serving, base_url, key)
    )
    return _stored(request, failed, outputs, tar_over).to_json()


def _stored(request: RolloutRequest, failed: str | None, outputs: dict[str, bytes | None], tar_over: bool) -> Rollout:
    """Decide what the rollout was (`settled`), then hash and store its outputs under its own id.
    The event log and bh-02's log are kept even when the lab failed, for finding out why.

    Raises:
        LabError: The lab failed the rollout (`failed` says why).
    """
    run_id = uuid.uuid4().hex
    root = Path("/artefacts")
    for folder, name in (("events", "events.jsonl"), ("logs", "bh02.log")):
        path = root / folder / f"{run_id}.{Path(name).suffix.lstrip('.')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(outputs.get(name) or b"")
    artefacts.commit()
    if failed is not None:
        raise LabError(f"{failed}; rollout {run_id}'s logs are in {ARTEFACTS}")
    entries, ended = settled(outputs.get("result.json"), outputs.get("work.tar"), tar_over)
    snapshot = tree_digest(entries)
    transcript_bytes = outputs.get("transcript.jsonl") or b""
    transcript = digest(transcript_bytes)
    for path, content in (
        (root / "snapshots" / f"{snapshot}.tar", packed(entries)),
        (root / "transcripts" / f"{transcript}.jsonl", transcript_bytes),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    artefacts.commit()
    return Rollout(request, run_id, snapshot, transcript, ended)


@app.function(image=runner_image, timeout=30 * 60)
def image_digest() -> str:
    """The digest of the pinned rollout image, building it if it isn't built."""
    return _digest()


@app.function(image=runner_image, timeout=30 * 60)
def probe_jail(jail: str = "bh-02", runtime: str = "gvisor") -> dict[str, Any]:
    """TASK-0001.04: can bh-02 run, and isolate the model's code, inside a Modal Sandbox? Runs
    bh-02 headless on its own fake model with `jail` ("bh-02": its own jail; "sandbox": no jail,
    a PID namespace of its own if the sandbox allows) on `runtime` ("gvisor" or "vm"), and
    returns what happened: the smoke run's log when bh-02 could not start, else the jail's status
    line, the result and bh-02's log."""
    payload = {
        "prompt": "import os; print(os.getuid())",
        "provider": "bh_02.testing:repl_provider",
        "model": "repl",
        "budget_s": 120,
        "jail": jail,
        "plugins": [],
    }
    failed, outputs, _ = _sandbox_run(payload, [], None, key="", serving=lambda: True, runtime=runtime)
    text = {name: (outputs.get(name) or b"").decode(errors="replace") for name in (*_OUTPUTS, "smoke.log")}
    return {
        "jail": jail,
        "runtime": runtime,
        "failed": failed,
        "smoke_log": text["smoke.log"][-3000:],
        "result": text["result.json"],
        "jail_status": [line for line in text["events.jsonl"].splitlines() if '"field": "jail"' in line],
        "events": text["events.jsonl"][-3000:],
        "log": text["bh02.log"][-3000:],
    }


@app.local_entrypoint()
def probe(jail: str = "bh-02", runtime: str = "gvisor") -> None:
    """`modal run -m bh02_lab.modal_apps.rollout::probe [--jail sandbox] [--runtime vm]`: run
    `probe_jail` and print what it found (a function run on its own prints nothing it returns)."""
    import json as json_module

    print(json_module.dumps(probe_jail.remote(jail, runtime), indent=2))
