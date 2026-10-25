"""The sandbox program's prepare step refuses to go on when bh-02 can't run here: a jail that
won't start is found before the model's code exists, so it is the lab's failure."""

import json
import subprocess
import sys
from pathlib import Path

from bh02_lab.sandbox import entry


def test_prepare_fails_when_bh02_cannot_run(tmp_path: Path) -> None:
    bh02 = tmp_path / "bh-02"
    script = bh02 / ".venv" / "bin" / "bh-02"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho 'bwrap: loopback: Failed RTM_NEWADDR' >&2\nexit 1\n")  # a jail that won't start
    script.chmod(0o755)
    lab, work = tmp_path / "lab", tmp_path / "work"
    lab.mkdir()
    work.mkdir()
    request = {"prompt": "x", "provider": "openai", "base_url": "https://h/v1", "model": "m", "budget_s": 60,
               "jail": "bh-02", "plugins": []}  # fmt: skip
    (lab / "request.json").write_text(json.dumps(request))
    done = subprocess.run(
        [sys.executable, entry.__file__, "prepare", "--lab", str(lab), "--work", str(work), "--bh02", str(bh02)],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode != 0 and "before any of the model's code existed" in done.stderr
    assert "RTM_NEWADDR" in (lab / "out" / "smoke.log").read_text()
    assert not (lab / "out" / "result.json").exists()
