"""What the lab's Modal apps share: the names of their apps, stores and secrets, and the image
every trusted lab function runs in.

Credentials, by where they may go (the plan's trust boundaries):
- `bh02-lab-inference` (`ORNITH_KEY`): the serving endpoint's key. The serving app and the rollout
  sandbox hold it; inside the sandbox bh-02's jail keeps it from the model's code.
- `bh02-lab-gate` (`GATE_TOKEN`): the one token the lab lead holds. Only the gate checks it.
- Modal's own credentials stay in Modal: functions call each other by name, and no sandbox the
  model runs in is given a token or a volume.
"""

from collections.abc import Mapping, Sequence
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

LAB_ROOT = Path("/lab")
_SHIPPED = LAB_ROOT / "repo"  # the parts of the repository a lab image carries, at their paths


def repo_root() -> Path:
    """The repository: the checkout when deploying, the image's copy of the parts it carries in a
    container. Image definitions read their local files from here, so a sandbox image defined
    again inside a container finds the same files and is the same image."""
    return _SHIPPED if _SHIPPED.is_dir() else PINS.parent


def pins() -> Pins:
    """The pins, from wherever `repo_root` is."""
    return load(repo_root() / "pins.toml")


def lab_image(
    *extra_packages: str, env: Mapping[str, str] | None = None, ship: Mapping[str, Sequence[str]] | None = None
) -> modal.Image:
    """The image trusted lab code runs in: Python 3.13, `extra_packages`, `env`, then the lab
    package, pins.toml, and what `ship` names (repository paths it carries under `repo_root`, each
    with the patterns left out of it). It holds no bh-02.

    The local files come last and are added when a container starts: Modal refuses a build step
    after them. So an image that needs more build steps takes them as arguments here, never as
    calls on the image this returns."""
    image = modal.Image.debian_slim(python_version="3.13").uv_pip_install("httpx>=0.27", *extra_packages)
    if env:
        image = image.env(dict(env))
    image = image.add_local_file(repo_root() / "pins.toml", str(_SHIPPED / "pins.toml")).add_local_python_source(
        "bh02_lab"
    )
    for path, leave_out in (ship or {}).items():
        source = repo_root() / path
        if source.is_dir():
            image = image.add_local_dir(source, str(_SHIPPED / path), ignore=["**/__pycache__/**", *leave_out])
        else:
            image = image.add_local_file(source, str(_SHIPPED / path))
    return image
