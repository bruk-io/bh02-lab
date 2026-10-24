"""The sandbox's program run outside a sandbox, against a local bh-02 checkout at the pinned SHA
with the headless plugin installed (`BH02_CHECKOUT`; `scripts/check` sets it), and bh-02's fake
repl model in place of Ornith. Proves the request becomes a headless run and the outputs the
runner reads are there and well formed."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bh02_lab.sandbox import entry
from bh02_lab.snapshot import entries_of

CHECKOUT = Path(os.environ.get("BH02_CHECKOUT", "/nonexistent"))

pytestmark = pytest.mark.skipif(
    not (CHECKOUT / ".venv" / "bin" / "bh-02").exists(), reason="no bh-02 checkout (set BH02_CHECKOUT)"
)


def _run(tmp_path: Path, prompt: str, jail: str) -> tuple[Path, dict[str, object]]:
    lab, work = tmp_path / "lab", tmp_path / "work"
    lab.mkdir()
    work.mkdir()
    (work / "pager.py").write_text("x = 1\n")
    request = {
        "prompt": prompt,
        "provider": "bh_02.testing:repl_provider",
        "model": "repl",
        "budget_s": 60,
        "jail": jail,
        "plugins": [],
    }
    (lab / "request.json").write_text(json.dumps(request))
    done = subprocess.run(
        [sys.executable, entry.__file__, "--lab", str(lab), "--work", str(work), "--bh02", str(CHECKOUT)],
        capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return lab / "out", json.loads((lab / "out" / "result.json").read_text())


def test_a_run_leaves_the_workspace_transcript_and_events(tmp_path: Path) -> None:
    out, result = _run(tmp_path, "open('answer.txt', 'w').write('42')", "sandbox")
    assert result == {"exit_code": 0, "ended": "replied"}, (out / "bh02.log").read_text()
    files = {e.path: e.content for e in entries_of((out / "work.tar").read_bytes())}
    assert files["answer.txt"] == b"42" and "pager.py" in files
    assert (out / "transcript.jsonl").read_text().strip()
    kinds = [json.loads(line)["kind"] for line in (out / "events.jsonl").read_text().splitlines()]
    assert kinds[-1] == "end" and "confirm" in kinds


def test_a_local_env_already_there_is_put_back(tmp_path: Path) -> None:
    local_env = CHECKOUT / "local.env"
    before = local_env.read_bytes() if local_env.exists() else None
    local_env.write_text("MINE=1\n")
    try:
        _run(tmp_path, "print('hi')", "sandbox")
        assert local_env.read_text() == "MINE=1\n"
    finally:
        if before is None:
            local_env.unlink()
        else:
            local_env.write_bytes(before)


def test_caches_and_environments_stay_out_of_the_tar(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, "import os; os.makedirs('.venv/lib'); open('.venv/lib/x', 'w').write('x')", "sandbox")
    import tarfile

    with tarfile.open(out / "work.tar") as archive:
        assert not [name for name in archive.getnames() if ".venv" in name]


_STUB = """
import json, sys
from models_cordis_plugin.openai.testing import StubServer
with StubServer(key=sys.argv[1]) as server:
    print(server.base_url, flush=True)
    sys.stdin.read()
    print(json.dumps([{"path": r["path"], "authorization": r.get("authorization")} for r in server.requests]))
"""


@pytest.mark.skipif(not Path("/usr/bin/bwrap").exists(), reason="bubblewrap is not installed")
def test_the_key_reaches_the_server_and_not_the_models_code(tmp_path: Path) -> None:
    key = "sk-lab-test-0123456789"
    stub = subprocess.Popen(
        [str(CHECKOUT / ".venv" / "bin" / "python"), "-c", _STUB, key],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    assert stub.stdout is not None and stub.stdin is not None
    base_url = stub.stdout.readline().strip()
    lab, work = tmp_path / "lab", tmp_path / "work"
    lab.mkdir()
    work.mkdir()
    steal = f"call print(open({str(CHECKOUT / 'local.env')!r}).read())"
    request = {"prompt": steal, "provider": "openai", "base_url": base_url, "model": "m", "budget_s": 60,
               "jail": "bh-02", "plugins": []}  # fmt: skip
    (lab / "request.json").write_text(json.dumps(request))
    key_file = tmp_path / "key"
    key_file.write_text(key)
    done = subprocess.run(
        [sys.executable, entry.__file__, "--lab", str(lab), "--work", str(work), "--bh02", str(CHECKOUT),
         "--key-file", str(key_file)],
        capture_output=True, text=True, timeout=180, check=False,
    )  # fmt: skip
    assert not key_file.exists()
    stub.stdin.close()
    sent = json.loads(stub.stdout.read().strip().splitlines()[-1])
    stub.wait(10)
    assert done.returncode == 0, done.stderr
    out = lab / "out"
    assert json.loads((out / "result.json").read_text())["ended"] == "replied", (out / "bh02.log").read_text()
    assert sent and all(r["authorization"] == f"Bearer {key}" for r in sent)
    results = [
        record["event"]["content"]
        for record in map(json.loads, (out / "events.jsonl").read_text().splitlines())
        if record["kind"] == "event" and record["event"].get("type") == "tool_result"
    ]
    assert results and "Traceback" in results[0]  # the read was tried, and the jail refused it
    for name in ("transcript.jsonl", "events.jsonl", "work.tar"):
        assert key.encode() not in (out / name).read_bytes(), name
