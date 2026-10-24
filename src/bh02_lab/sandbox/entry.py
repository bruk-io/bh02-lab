"""The program a rollout sandbox runs: set bh-02 up for one task, run it headless, and leave the
workspace, the transcript and the event log where the runner reads them.

It runs inside the untrusted sandbox, with the standard library only (it is copied into the image
on its own, so it imports nothing of the lab). It is handed one JSON file (`LAB/request.json`) and
the inference key in a file (`--key-file`), which it removes once read. The key goes into bh-02's
`local.env`, beside bh-02's own install, where bh-02's model row reads it and its jail keeps the
model's code from it; it is never in an environment. With `jail = "sandbox"` there is no jail
inside the sandbox, and the model's code can read the key while bh-02 runs.

Everything it writes for the runner is under `LAB/out`. The runner trusts none of it: it hashes and
re-packs the workspace itself and decides what the run was from the bytes.

`models_toml`, `patch_toml` and `ended` are pure; `main` does the work.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = ["ended", "main", "models_toml", "patch_toml"]

KEY = "ORNITH_KEY"


def _toml_text(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def models_toml(request: Mapping[str, Any]) -> str:
    """bh-02's models file for the run: one endpoint, `lab`, serving the one model.

    >>> print(models_toml({"provider": "openai", "base_url": "https://h/v1", "model": "M", "temperature": 0.6}))
    default = "lab/model"
    <BLANKLINE>
    [lab]
    provider = "openai"
    base_url = "https://h/v1"
    key = "ORNITH_KEY"
    <BLANKLINE>
    [lab.models.model]
    id = "M"
    temperature = 0.6
    <BLANKLINE>
    """
    lines = ['default = "lab/model"', "", "[lab]", f"provider = {_toml_text(request['provider'])}"]
    if request.get("base_url"):
        lines += [f"base_url = {_toml_text(request['base_url'])}", f'key = "{KEY}"']
    lines += ["", "[lab.models.model]", f"id = {_toml_text(request['model'])}"]
    if request.get("temperature") is not None:
        lines.append(f"temperature = {float(request['temperature'])}")
    return "\n".join(lines) + "\n"


def patch_toml(task: Path, events: Path, budget_s: float, confirm: str) -> str:
    """The layer that puts the headless row in place of bh-02's terminal app."""
    config = (
        f"task = {_toml_text(str(task))}, events = {_toml_text(str(events))}, "
        f"budget_s = {float(budget_s)}, confirm = {_toml_text(confirm)}"
    )
    return f'[[plugin]]\nid = "ui"\nuse = "headless:ui"\nconfig = {{ {config} }}\n'


def ended(event_lines: Iterable[str], exit_code: int) -> str:
    """How the run ended: the headless row's own `end` record (`replied` or `budget`), else
    `crashed`, which is also what a non-zero exit is.

    >>> ended(['{"kind": "task"}', '{"kind": "end", "reason": "replied"}'], 0)
    'replied'
    >>> ended(['{"kind": "task"}'], 0), ended(['{"kind": "end", "reason": "budget"}'], 1)
    ('crashed', 'crashed')
    """
    if exit_code != 0:
        return "crashed"
    for text in reversed(list(event_lines)):
        try:
            record = json.loads(text)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("kind") == "end" and record.get("reason") in ("replied", "budget"):
            return str(record["reason"])
    return "crashed"


# The same directories `bh02_lab.hashing.kept` leaves out (a test keeps the two equal): left out
# here too, so a workspace with an environment in it is not a large tar for nothing.
SKIPPED_DIRS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "mypy_cache",
        ".git",
        ".venv",
        "venv",
        "node_modules",
    }
)


def _kept_member(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """A tar member, or None for a link, a device, or anything under a skipped directory."""
    parts = Path(member.name).parts
    if any(part in SKIPPED_DIRS for part in parts) or not (member.isreg() or member.isdir()):
        return None
    return member


def _stop_strays() -> None:
    """Kill every process but this one and its ancestors, so nothing the model started outlives
    bh-02 and changes the workspace or the outputs while they are read."""
    keep = {os.getpid(), 1}
    pid = os.getppid()
    while pid > 1 and pid not in keep:
        keep.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and int(entry.name) not in keep:
            try:
                if entry.stat().st_uid == os.getuid():
                    os.kill(int(entry.name), signal.SIGKILL)
            except OSError:
                pass


def _write_genome(work: Path, plugins: Iterable[Mapping[str, str]]) -> None:
    directory = work / ".bh-02" / "plugins"
    for plugin in plugins:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{plugin['name']}.py").write_text(plugin["source"], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Run the task in `LAB/request.json`; always leave `LAB/out/result.json`, whatever bh-02 did."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab", type=Path, default=Path("/lab"))
    parser.add_argument("--work", type=Path, default=Path("/work"))
    parser.add_argument("--bh02", type=Path, default=Path("/opt/bh-02"))
    parser.add_argument("--key-file", type=Path, default=None, help="the inference key; read, then removed")
    parser.add_argument(
        "--stop-strays",
        action="store_true",
        help="in the sandbox only: kill every other process of this user after bh-02",
    )
    args = parser.parse_args(argv)
    lab, work, bh02 = args.lab, args.work, args.bh02
    out = lab / "out"
    request = json.loads((lab / "request.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    config, state, home = lab / "config", lab / "state", lab / "home"
    for directory in (config / "bh-02", state, home):
        directory.mkdir(parents=True, exist_ok=True)
    (config / "bh-02" / "models.toml").write_text(models_toml(request), encoding="utf-8")
    key = ""
    if args.key_file is not None and args.key_file.exists():
        key = args.key_file.read_text(encoding="utf-8").strip()
        args.key_file.unlink()
    local_env = bh02 / "local.env"
    kept_env = local_env.read_bytes() if local_env.exists() else None  # a developer's own, put back after
    local_env.write_text(f"{KEY}={key}\n", encoding="utf-8")
    local_env.chmod(0o600)
    task = lab / "task.md"
    task.write_text(request["prompt"], encoding="utf-8")
    events = out / "events.jsonl"
    jailed = request["jail"] == "bh-02"
    patch = lab / "headless.toml"
    patch.write_text(patch_toml(task, events, request["budget_s"], "deny" if jailed else "allow"), encoding="utf-8")
    _write_genome(work, request.get("plugins", ()))
    environment = {
        "PATH": f"{bh02 / '.venv' / 'bin'}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(state),
        "LANG": "C.UTF-8",
    }
    command = [str(bh02 / ".venv" / "bin" / "bh-02"), "--patch", str(patch), *([] if jailed else ["--no-jail"])]
    with (out / "bh02.log").open("w", encoding="utf-8") as log:
        try:
            done = subprocess.run(
                command, cwd=work, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                timeout=float(request["budget_s"]) + 300, check=False,
            )  # fmt: skip
            code = done.returncode
        except subprocess.TimeoutExpired:
            code = -9
    if args.stop_strays:
        _stop_strays()
    local_env.unlink(missing_ok=True)
    if kept_env is not None:
        local_env.write_bytes(kept_env)
    transcripts = sorted(state.glob("bh-02/sessions/*/transcript.jsonl"))
    if transcripts:
        (out / "transcript.jsonl").write_bytes(transcripts[-1].read_bytes())
    with tarfile.open(out / "work.tar", "w") as archive:
        archive.add(work, arcname=".", filter=_kept_member)
    lines = events.read_text(encoding="utf-8").splitlines() if events.exists() else []
    (out / "result.json").write_text(json.dumps({"exit_code": code, "ended": ended(lines, code)}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
