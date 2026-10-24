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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import modal

from bh02_lab.battery import load_battery
from bh02_lab.contract import Rollout, RolloutRequest
from bh02_lab.hashing import digest, tree_digest
from bh02_lab.modal_apps.common import APPS, ARTEFACTS, INFERENCE_SECRET, LAB_ROOT, lab_image, pins, repo_root
from bh02_lab.pins import Pins, serving_hash
from bh02_lab.snapshot import SnapshotError, entries_of, packed

__all__ = ["app", "image_digest", "probe_jail", "run", "sandbox_request"]

_PINS = pins()
_HEADLESS = "/opt/headless"
_ENTRY = "/opt/lab/entry.py"
_SANDBOX_GRACE_S = 600

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
        .add_local_dir(repo_root() / "headless", _HEADLESS, copy=True, ignore=["tests", "**/__pycache__"])
        .run_commands(f"uv pip install --python /opt/bh-02/.venv/bin/python --no-deps {_HEADLESS}")
        .add_local_file(repo_root() / "src" / "bh02_lab" / "sandbox" / "entry.py", _ENTRY, copy=True)
        .run_commands("mkdir -p /work /lab")
    )


rollout_image = _rollout_image(_PINS)
runner_image = lab_image().add_local_dir(
    repo_root() / "battery", str(LAB_ROOT / "battery"), ignore=["**/check/**", "**/reference/**"]
)


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
    """The genome's plugin sources from the volume, each checked against the hash that names it."""
    found = []
    for plugin_hash in sorted(genome):
        source = Path(f"/artefacts/genome/{plugin_hash}.py").read_bytes()
        if digest(source) != plugin_hash:
            raise ValueError(f"genome plugin {plugin_hash} does not hash to its name; it was changed after it was stored")
        found.append({"name": f"g_{plugin_hash[:12]}", "source": source.decode()})
    return found


def _sandbox_run(
    payload: dict[str, Any], repo: list[tuple[str, bytes, bool]], allow: list[str] | None
) -> dict[str, bytes]:
    """Start a fresh sandbox, run the task, read its outputs back, and end it. `allow`: the only
    domains the sandbox may reach (None: no limit, for the probe alone)."""
    sandbox = modal.Sandbox.create(
        "sleep", "infinity",
        app=app,
        image=rollout_image,
        secrets=[modal.Secret.from_name(INFERENCE_SECRET)],
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
        sandbox.exec("python3", _ENTRY, timeout=int(payload["budget_s"]) + _SANDBOX_GRACE_S - 60).wait()
        outputs = {}
        for name in ("result.json", "work.tar", "transcript.jsonl", "events.jsonl", "bh02.log"):
            try:
                outputs[name] = files.read_bytes(f"/lab/out/{name}")
            except Exception:  # noqa: BLE001 - a missing output is recorded as missing, whatever the sandbox did
                outputs[name] = b""
        return outputs
    finally:
        sandbox.terminate()


@app.function(image=runner_image, volumes={"/artefacts": artefacts}, timeout=2 * 60 * 60, max_containers=32)
def run(request_json: dict[str, Any], battery: str = "core") -> dict[str, Any]:
    """Run one rollout of a task of `battery` and store what it left; returns the `Rollout` as JSON.

    Raises:
        ValueError: The request names another image or model than the pinned ones, or a task the
            battery does not have.
    """
    request = RolloutRequest.from_json(request_json)
    built = _digest()
    if request.image_digest != built:
        raise ValueError(f"the request names image {request.image_digest}, but the pinned image is {built}")
    if request.model != serving_hash(_PINS):
        raise ValueError(f"the request names model {request.model}, but the pinned model is {serving_hash(_PINS)}")
    tasks = load_battery(battery, LAB_ROOT / "battery", with_checks=False)
    task = tasks.get(request.task_id)
    if task is None:
        raise ValueError(f"battery {battery!r} has no task {request.task_id!r}")
    base_url = _base_url()
    payload = sandbox_request(request, task.prompt, base_url, _plugins(request.genome), _PINS)
    repo = [(e.path, e.content, e.executable) for e in task.repo]
    outputs = _sandbox_run(payload, repo, [urlparse(base_url).hostname or ""])
    return _stored(request, outputs).to_json()


def _stored(request: RolloutRequest, outputs: dict[str, bytes]) -> Rollout:
    """Hash and store the outputs on the trusted side; the sandbox's own claims are not read but
    for how it ended, and a workspace that can't be read is stored as an empty one."""
    try:
        result = json.loads(outputs["result.json"] or b"{}")
        ended = str(result.get("ended", "crashed"))
    except ValueError:
        ended = "crashed"
    try:
        entries = entries_of(outputs["work.tar"])
    except SnapshotError as error:
        entries, ended = [], f"unreadable: {error}"
    snapshot = tree_digest(entries)
    transcript = digest(outputs["transcript.jsonl"])
    root = Path("/artefacts")
    for path, content in (
        (root / "snapshots" / f"{snapshot}.tar", packed(entries)),
        (root / "transcripts" / f"{transcript}.jsonl", outputs["transcript.jsonl"]),
        (root / "events" / f"{snapshot}.{request.task_id}.{request.seed}.jsonl", outputs["events.jsonl"]),
        (root / "logs" / f"{snapshot}.{request.task_id}.{request.seed}.log", outputs["bh02.log"]),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    artefacts.commit()
    return Rollout(request, snapshot, transcript, ended)


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
    outputs = _sandbox_run(payload, [], None)
    jail = [
        line
        for line in outputs["events.jsonl"].decode(errors="replace").splitlines()
        if '"field": "jail"' in line
    ]
    return {
        "result": outputs["result.json"].decode(errors="replace"),
        "jail": jail,
        "events": outputs["events.jsonl"].decode(errors="replace")[-4000:],
        "log": outputs["bh02.log"].decode(errors="replace")[-4000:],
    }
