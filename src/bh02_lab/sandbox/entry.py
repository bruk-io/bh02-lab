"""The program a rollout sandbox runs: set bh-02 up for one task, run it headless, and leave the
workspace, the transcript and the event log where the runner reads them.

It runs in two steps, two separate commands, so the runner can tell the lab's failures from the
model's: `prepare` sets everything up before any of the model's code exists (its failure is the
lab's), and `run` starts bh-02 and packs up afterwards (anything that goes wrong from there on
counts against the model, since the model's code may have caused it). `all` does both, for
tests.

It runs inside the untrusted sandbox, with the standard library only (it is copied into the image
on its own, so it imports nothing of the lab). It is handed one JSON file (`LAB/request.json`) and
the inference key in a file (`--key-file`), which it removes once read. The key goes into bh-02's
`local.env`, beside bh-02's own install, where bh-02's model row reads it and its jail keeps the
model's code from it; it is never in an environment. With `jail = "sandbox"` there is no jail
inside the sandbox, and the model's code can read the key while bh-02 runs.

Everything it writes for the runner is under `LAB/out`. The runner trusts none of it: it hashes and
re-packs the workspace itself and decides what the run was from the bytes. The workspace is packed
by walking it without recursion, under the same caps a snapshot has; a workspace over them is
`oversized` and no tar is written, so no workspace can make packing fail. `prepare` sets aside
disk space that `run` frees before it writes, so a model that fills the disk can't stop the
result being written.

Without bh-02's own jail (`jail = "sandbox"`), bh-02 runs in a PID namespace of its own when the
sandbox allows one: when bh-02 exits, the kernel ends everything the model started, so nothing
changes the workspace while it is packed. Whether it did is in `result.json` as `isolated`.

`models_toml`, `patch_toml`, `ended` and `packable` are pure; the steps do the work.
"""

import argparse
import json
import os
import stat
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_BYTES",
    "MAX_DEPTH",
    "MAX_FILES",
    "SKIPPED_DIRS",
    "ended",
    "main",
    "models_toml",
    "packable",
    "patch_toml",
]

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


# The same directories and caps `bh02_lab.hashing.kept` and `bh02_lab.snapshot` use (tests keep
# them equal): a workspace this program packs is one the runner can take as a snapshot.
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
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000
MAX_DEPTH = 64
_MAX_TRANSCRIPT = 32 * 1024 * 1024
_RESERVE = 16 * 1024 * 1024


def packable(root: Path) -> tuple[list[tuple[Path, str]], str | None]:
    """The workspace's files to pack (path, name in the tar), walked without recursion and
    without following links, or why it is over the caps (then no files).

    >>> packable(Path("/nonexistent"))
    ([], None)
    """
    files: list[tuple[Path, str]] = []
    total = 0
    for directory, subdirectories, names in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIPPED_DIRS)
        if len(relative.parts) >= MAX_DEPTH and subdirectories:
            return [], f"deeper than {MAX_DEPTH} directories"
        for name in sorted(names):
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue  # links, devices, sockets: not part of a snapshot
            total += info.st_size
            files.append((path, (relative / name).as_posix()))
            if len(files) > MAX_FILES:
                return [], f"over {MAX_FILES} files"
            if total > MAX_BYTES:
                return [], f"over {MAX_BYTES} bytes"
    return files, None


def _write_genome(work: Path, plugins: Iterable[Mapping[str, str]]) -> None:
    directory = work / ".bh-02" / "plugins"
    for plugin in plugins:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{plugin['name']}.py").write_text(plugin["source"], encoding="utf-8")


def _isolating(command: list[str]) -> tuple[list[str], bool]:
    """`command` in a PID namespace of its own, when `unshare` can make one here."""
    probe = ["unshare", "--pid", "--fork", "--kill-child", "true"]
    try:
        works = subprocess.run(probe, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        works = False
    return (["unshare", "--pid", "--fork", "--kill-child", *command], True) if works else (command, False)


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    lab = args.lab
    return {
        "lab": lab, "work": args.work, "bh02": args.bh02, "out": lab / "out", "config": lab / "config",
        "state": lab / "state", "home": lab / "home", "task": lab / "task.md", "patch": lab / "headless.toml",
        "local_env": args.bh02 / "local.env", "kept_env": lab / "kept-local.env", "reserve": lab / "reserve",
    }  # fmt: skip


def prepare(args: argparse.Namespace) -> None:
    """Set bh-02 up for the request, before any of the model's code exists."""
    at = _paths(args)
    request = json.loads((at["lab"] / "request.json").read_text(encoding="utf-8"))
    for directory in (at["out"], at["config"] / "bh-02", at["state"], at["home"]):
        directory.mkdir(parents=True, exist_ok=True)
    (at["config"] / "bh-02" / "models.toml").write_text(models_toml(request), encoding="utf-8")
    key = ""
    if args.key_file is not None and args.key_file.exists():
        key = args.key_file.read_text(encoding="utf-8").strip()
        args.key_file.unlink()
    if at["local_env"].exists():  # a developer's own, put back after the run
        at["kept_env"].write_bytes(at["local_env"].read_bytes())
    at["local_env"].write_text(f"{KEY}={key}\n", encoding="utf-8")
    at["local_env"].chmod(0o600)
    at["task"].write_text(request["prompt"], encoding="utf-8")
    confirm = "deny" if request["jail"] == "bh-02" else "allow"
    at["patch"].write_text(
        patch_toml(at["task"], at["out"] / "events.jsonl", request["budget_s"], confirm), encoding="utf-8"
    )
    _write_genome(at["work"], request.get("plugins", ()))
    (at["out"] / "result.json").write_text(json.dumps({"exit_code": None, "ended": "crashed"}), encoding="utf-8")
    with at["reserve"].open("wb") as file:
        file.write(b"\0" * _RESERVE)


def run(args: argparse.Namespace) -> None:
    """Run bh-02 on the prepared task, then leave the transcript, the workspace and the result."""
    at = _paths(args)
    request = json.loads((at["lab"] / "request.json").read_text(encoding="utf-8"))
    jailed = request["jail"] == "bh-02"
    environment = {
        "PATH": f"{at['bh02'] / '.venv' / 'bin'}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(at["home"]), "XDG_CONFIG_HOME": str(at["config"]), "XDG_STATE_HOME": str(at["state"]),
        "LANG": "C.UTF-8",
    }  # fmt: skip
    command = [str(at["bh02"] / ".venv" / "bin" / "bh-02"), "--patch", str(at["patch"])]
    isolated = jailed
    if not jailed:
        command, isolated = _isolating([*command, "--no-jail"])
    try:
        with (at["out"] / "bh02.log").open("w", encoding="utf-8") as log:
            done = subprocess.run(
                command, cwd=at["work"], env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                timeout=float(request["budget_s"]) + 300, check=False,
            )  # fmt: skip
        code: int | None = done.returncode
    except (OSError, subprocess.TimeoutExpired):
        code = None
    at["reserve"].unlink(missing_ok=True)  # the room to write what follows, whatever the model filled
    at["local_env"].unlink(missing_ok=True)
    if at["kept_env"].exists():
        at["local_env"].write_bytes(at["kept_env"].read_bytes())
        at["kept_env"].unlink()
    _keep_transcript(at["state"], at["out"])
    files, over = packable(at["work"])
    if over is None:
        with tarfile.open(at["out"] / "work.tar", "w") as archive:
            for path, name in files:
                archive.add(path, arcname=name, recursive=False)
    events = at["out"] / "events.jsonl"
    lines = events.read_text(encoding="utf-8", errors="replace").splitlines() if events.exists() else []
    result = {"exit_code": code, "ended": "oversized" if over else ended(lines, code if code is not None else -1)}
    (at["out"] / "result.json").write_text(json.dumps(result | {"isolated": isolated, "over": over}), encoding="utf-8")


def _keep_transcript(state: Path, out: Path) -> None:
    transcripts = sorted(state.glob("bh-02/sessions/*/transcript.jsonl"))
    if transcripts and transcripts[-1].stat().st_size <= _MAX_TRANSCRIPT:
        (out / "transcript.jsonl").write_bytes(transcripts[-1].read_bytes())


def main(argv: list[str] | None = None) -> int:
    """`prepare`, `run`, or `all` (both)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("step", nargs="?", choices=("prepare", "run", "all"), default="all")
    parser.add_argument("--lab", type=Path, default=Path("/lab"))
    parser.add_argument("--work", type=Path, default=Path("/work"))
    parser.add_argument("--bh02", type=Path, default=Path("/opt/bh-02"))
    parser.add_argument("--key-file", type=Path, default=None, help="the inference key; read, then removed")
    args = parser.parse_args(argv)
    if args.step in ("prepare", "all"):
        prepare(args)
    if args.step in ("run", "all"):
        run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
