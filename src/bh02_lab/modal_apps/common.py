"""What the lab's Modal apps share: the names of their apps, stores and secrets, and the image
every trusted lab function runs in.

Credentials, by where they may go (the plan's trust boundaries):
- `bh02-lab-inference` (`ORNITH_KEY`): the serving endpoint's key. The serving app and the rollout
  sandbox hold it; inside the sandbox bh-02's jail keeps it from the model's code.
- `bh02-lab-gate` (`GATE_TOKEN`): the one token the lab lead holds. Only the gate checks it.
- Modal's own credentials stay in Modal: functions call each other by name, and no sandbox the
  model runs in is given a token or a volume.
"""

from pathlib import Path

import modal

from bh02_lab.pins import PINS, Pins, load

__all__ = [
    "ARTEFACTS",
    "BATTERY_JOBS",
    "GATE_SECRET",
    "INFERENCE_SECRET",
    "LAB_ROOT",
    "VERDICTS",
    "lab_image",
    "pins",
    "repo_root",
]

APPS = {
    "serve": "bh02-lab-serve",
    "rollout": "bh02-lab-rollout",
    "judge": "bh02-lab-judge",
    "gate": "bh02-lab-gate",
    "train": "bh02-lab-train",
}
ARTEFACTS = "bh02-lab-artefacts"  # Volume: snapshots, transcripts, event logs, genome plugins
VERDICTS = "bh02-lab-verdicts"  # Dict: the judge's own store, verdicts by `Verdict.key`
BATTERY_JOBS = "bh02-lab-batteries"  # Dict: the gate's battery runs, by id
INFERENCE_SECRET = "bh02-lab-inference"
GATE_SECRET = "bh02-lab-gate"

LAB_ROOT = Path("/lab")  # where a lab image keeps pins.toml and the batteries
_IN_IMAGE_PINS = LAB_ROOT / "pins.toml"


def repo_root() -> Path:
    """The repository, when this runs from a checkout (deploying); in a container it is not there."""
    return PINS.parent


def pins() -> Pins:
    """The pins: the image's copy in a container, the repository's when deploying."""
    return load(_IN_IMAGE_PINS if _IN_IMAGE_PINS.exists() else PINS)


def lab_image(*extra_packages: str) -> modal.Image:
    """The image trusted lab code runs in: Python 3.13, the lab package, pins.toml, and
    `extra_packages`. It holds no bh-02."""
    return (
        modal.Image.debian_slim(python_version="3.13")
        .uv_pip_install("httpx>=0.27", *extra_packages)
        .add_local_file(PINS, str(_IN_IMAGE_PINS))
        .add_local_python_source("bh02_lab")
    )
