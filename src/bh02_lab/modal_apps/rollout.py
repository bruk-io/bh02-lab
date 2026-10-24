"""Rollouts: bh-02 at its pinned SHA, headless, in a single-use Modal Sandbox per request.

Two images. The rollout image is what runs untrusted: bh-02 at the pinned SHA with every plugin,
the headless row, and the sandbox's program; no lab code, no checks, no credential but the
inference key, which arrives as a secret. The runner function is trusted lab code: it starts the
sandbox, hands it the request and the task's public part, and takes back the workspace, the
transcript and the event log, which it hashes and stores itself.

The image is pinned by the id Modal gives the built image (its digest here); a request for any
other digest is refused, so a rollout always ran on the image it names.

Deploy: `modal deploy -m bh02_lab.modal_apps.rollout`.
Spike (TASK-0001.04): `modal run -m bh02_lab.modal_apps.rollout::probe_jail`.
"""

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import modal

from bh02_lab.battery import load_battery
from bh02_lab.contract import Rollout, RolloutRequest
from bh02_lab.hashing import digest, tree_digest
from bh02_lab.modal_apps.common import APPS, ARTEFACTS, INFERENCE_SECRET, lab_image, pins, repo_root
from bh02_lab.pins import Pins, serving_hash
from bh02_lab.rollout_core import LabError, settled
from bh02_lab.snapshot import packed

__all__ = ["app", "image_digest", "probe_jail", "run", "sandbox_request"]

_PINS = pins()
_HEADLESS = "/opt/headless"
_ENTRY = "/opt/lab/entry.py"
_ENTRY_SOURCE = "src/bh02_lab/sandbox/entry.py"
_LEFT_OUT = ["tests", "tests/**", "**/__pycache__/**", "**/.pytest_cache/**", "**/*.egg-info/**"]
_SANDBOX_GRACE_S = 600
_OUTPUTS = ("result.json", "work.tar", "transcript.jsonl", "events.jsonl", "bh02.log")

app = modal.App(APPS["rollout"])
artefacts = modal.Volume.from_name(ARTEFACTS, create_if_missing=True)


def _rollout_image(pinned: Pins) -> modal.Image:
    bh02 = pinned.bh02
    return (
        modal.Image.debian_slim(python_version="3.13")
        .apt_install("git", "bubblewrap", "ca-certificates")
        .pip_install("uv")
        .run_commands(
            f"git clone {bh02.repo} /opt/bh-02",
            f"git -C /opt/bh-02 checkout --detach {bh02.sha}",
            f"cd /opt/bh-02 && uv python install {bh02.python}",
            "cd /opt/bh-02 && uv sync --all-packages --frozen --no-dev",
        )
        .add_local_dir(repo_root() / "headless", _HEADLESS, copy=True, ignore=_LEFT_OUT)
        .run_commands(f"uv pip install --python /opt/bh-02/.venv/bin/python --no-deps {_HEADLESS}")
        .add_local_file(repo_root() / _ENTRY_SOURCE, _ENTRY, copy=True)
        .run_commands("mkdir -p /work /lab")
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


def _sandbox_run(
    payload: dict[str, Any], repo: list[tuple[str, bytes, bool]], allow: list[str] | None, key: str
) -> tuple[int | None, dict[str, bytes | None]]:
    """Start a fresh sandbox, run the task, read its outputs back, and end it: the program's exit
    code and each output (None when it isn't there). `allow`: the only domains the sandbox may
    reach (None: no limit, for the probe alone). The key goes in as a file the program removes,
    never as a secret or an environment variable of the sandbox."""
    sandbox = modal.Sandbox.create(
        "sleep", "infinity",
        app=app,
        image=rollout_image,
        timeout=int(payload["budget_s"]) + _SANDBOX_GRACE_S,
        cpu=2.0,
        memory=4096,
        workdir="/work",
        outbound_domain_allowlist=allow,
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
        process = sandbox.exec(
            "python3", _ENTRY, "--key-file", "/lab/key", "--stop-strays",
            timeout=int(payload["budget_s"]) + _SANDBOX_GRACE_S - 60,
        )  # fmt: skip
        process.wait()
        outputs: dict[str, bytes | None] = {}
        for name in _OUTPUTS:
            try:
                outputs[name] = files.read_bytes(f"/lab/out/{name}")
            except Exception:  # an output that isn't there; whether that matters is `settled`'s call
                outputs[name] = None
        return process.returncode, outputs
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
    code, outputs = _sandbox_run(payload, repo, [urlparse(base_url).hostname or ""], os.environ["ORNITH_KEY"])
    return _stored(request, code, outputs).to_json()


def _stored(request: RolloutRequest, code: int | None, outputs: dict[str, bytes | None]) -> Rollout:
    """Decide what the rollout was (`settled`), then hash and store its outputs under its own id.
    The event log and bh-02's log are kept even when the lab failed, for finding out why.

    Raises:
        LabError: The sandbox's program did not finish.
    """
    run_id = uuid.uuid4().hex
    root = Path("/artefacts")
    for folder, name in (("events", "events.jsonl"), ("logs", "bh02.log")):
        path = root / folder / f"{run_id}.{Path(name).suffix.lstrip('.')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(outputs.get(name) or b"")
    artefacts.commit()
    try:
        entries, ended = settled(code, outputs.get("result.json"), outputs.get("work.tar"))
    except LabError as error:
        error.add_note(f"rollout {run_id}: its event log and bh-02's log are in {ARTEFACTS}")
        raise
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
def probe_jail() -> dict[str, Any]:
    """TASK-0001.04: does bh-02's jail come up inside a Modal Sandbox? Runs bh-02 headless on its
    own fake model, jailed, and returns the jail's status line, the result and bh-02's log."""
    payload = {
        "prompt": "import os; print(os.getuid())",
        "provider": "bh_02.testing:repl_provider",
        "model": "repl",
        "budget_s": 120,
        "jail": "bh-02",
        "plugins": [],
    }
    code, outputs = _sandbox_run(payload, [], None, key="")
    text = {name: (outputs.get(name) or b"").decode(errors="replace") for name in _OUTPUTS if name != "work.tar"}
    return {
        "exit_code": code,
        "result": text["result.json"],
        "jail": [line for line in text["events.jsonl"].splitlines() if '"field": "jail"' in line],
        "events": text["events.jsonl"][-4000:],
        "log": text["bh02.log"][-4000:],
    }
