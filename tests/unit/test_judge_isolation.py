"""The judge never imports bh-02, the headless row, cordis or the rollout side: what judges a
snapshot can't be changed by what made it."""

import json
import subprocess
import sys

import pytest

FORBIDDEN = ("bh_02", "cordis", "headless_cordis_plugin", "bh02_lab.sandbox", "bh02_lab.modal_apps.rollout")


@pytest.mark.parametrize("module", ["bh02_lab.modal_apps.judge", "bh02_lab.battery", "bh02_lab.snapshot"])
def test_the_judge_side_imports_nothing_of_bh02(module: str) -> None:
    pytest.importorskip("modal")
    probe = f"import json, sys, {module}; print(json.dumps(sorted(sys.modules)))"
    loaded = json.loads(
        subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    )
    assert not [name for name in loaded if name.startswith(FORBIDDEN)]
