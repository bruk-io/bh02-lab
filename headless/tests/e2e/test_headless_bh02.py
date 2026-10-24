"""The real bh-02, installed beside this plugin, run headless through `--patch` against bh-02's
own fake model (`bh_02.testing:repl_provider`: the task's text is run as one python input). No
terminal, no credential: what a rollout does, minus Modal."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_BH02 = Path(sys.prefix) / "bin" / "bh-02"
_MODELS = """default = "fake/repl"
[fake]
provider = "bh_02.testing:repl_provider"
models = { repl = { id = "repl" } }
"""

pytestmark = pytest.mark.skipif(not _BH02.exists(), reason="bh-02 is not installed in this environment")


def _run(tmp_path: Path, *, jailed: bool, confirm: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    config, state, work = tmp_path / "config", tmp_path / "state", tmp_path / "work"
    for directory in (config / "bh-02", state, work):
        directory.mkdir(parents=True)
    (config / "bh-02" / "models.toml").write_text(_MODELS)
    task = tmp_path / "task.md"
    task.write_text("open('answer.txt', 'w').write('42'); print('wrote')")
    events = tmp_path / "events.jsonl"
    patch = tmp_path / "headless.toml"
    patch.write_text(
        '[[plugin]]\nid = "ui"\nuse = "headless:ui"\n'
        f'config = {{ task = "{task}", events = "{events}", budget_s = 60, confirm = "{confirm}" }}\n'
    )
    command = [str(_BH02), "--patch", str(patch), *([] if jailed else ["--no-jail"])]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(config), "XDG_STATE_HOME": str(state)}
    done = subprocess.run(
        command, cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False
    )
    return done, tmp_path


def _kinds(events: Path) -> list[str]:
    return [json.loads(text)["kind"] for text in events.read_text().splitlines()]


def _transcripts(state: Path) -> list[Path]:
    return sorted(state.glob("bh-02/sessions/*/transcript.jsonl"))


def test_an_unjailed_run_asks_and_runs_on_allow(tmp_path: Path) -> None:
    done, root = _run(tmp_path, jailed=False, confirm="allow")
    assert done.returncode == 0, done.stderr
    assert (root / "work" / "answer.txt").read_text() == "42"
    kinds = _kinds(root / "events.jsonl")
    assert kinds.count("task") == 1 and "confirm" in kinds and kinds[-1] == "end"
    assert len(_transcripts(root / "state")) == 1


def test_an_unjailed_run_changes_nothing_on_deny(tmp_path: Path) -> None:
    done, root = _run(tmp_path, jailed=False, confirm="deny")
    assert done.returncode == 0, done.stderr
    assert not (root / "work" / "answer.txt").exists()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
def test_a_jailed_run_asks_nothing(tmp_path: Path) -> None:
    done, root = _run(tmp_path, jailed=True, confirm="deny")
    assert done.returncode == 0, done.stderr
    assert (root / "work" / "answer.txt").read_text() == "42"
    assert "confirm" not in _kinds(root / "events.jsonl")
