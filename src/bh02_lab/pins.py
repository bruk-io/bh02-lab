"""`pins.toml` as values, and the hashes a rollout records from them.

The model a rollout ran on is the hash of everything that decides what the server answers: the
weights' revision, the vLLM version, its arguments, the server-side sampling and the temperature
bh-02 sends. Change any of them and it is a different model to the gate.

`parsed` and `serving_hash` are pure; `load` reads the file.
"""

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bh02_lab.hashing import digest

__all__ = ["Bh02", "Model", "Pins", "PinsError", "Rollout", "Serving", "load", "parsed", "serving_hash"]

PINS = Path(__file__).resolve().parents[2] / "pins.toml"
_JAILS = frozenset({"bh-02", "sandbox"})
_RUNTIMES = frozenset({"gvisor", "vm"})


class PinsError(ValueError):
    """`pins.toml` is missing a pin, or one has the wrong shape."""


@dataclass(frozen=True, slots=True)
class Bh02:
    """Where bh-02 comes from: its repository, the commit, the Python it runs on, and the uv
    that builds its environment."""

    repo: str
    sha: str
    python: str
    uv: str


@dataclass(frozen=True, slots=True)
class Model:
    """The weights served, by repository and revision, under the name bh-02 asks for."""

    repo: str
    revision: str
    served_name: str
    temperature: float


@dataclass(frozen=True, slots=True)
class Serving:
    """How vLLM serves them: its version, the GPU, the context, its arguments and its sampling."""

    vllm: str
    gpu: str
    max_model_len: int
    args: tuple[str, ...]
    generation: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Rollout:
    """How a rollout runs: its time budget, which jail holds the model's code, and the Modal
    Sandbox runtime it runs on."""

    budget_s: float
    jail: str
    runtime: str


@dataclass(frozen=True, slots=True)
class Pins:
    """Every pin in pins.toml."""

    bh02: Bh02
    model: Model
    serving: Serving
    rollout: Rollout


def parsed(data: Mapping[str, Any]) -> Pins:
    """The pins a parsed `pins.toml` holds.

    Raises:
        PinsError: A table or a key is missing, or a value is of the wrong type.
    """
    try:
        bh02, model, serving, rollout = (data[name] for name in ("bh02", "model", "serving", "rollout"))
        pins = Pins(
            bh02=Bh02(str(bh02["repo"]), str(bh02["sha"]), str(bh02["python"]), str(bh02["uv"])),
            model=Model(
                str(model["repo"]), str(model["revision"]), str(model["served_name"]), float(model["temperature"])
            ),
            serving=Serving(
                vllm=str(serving["vllm"]),
                gpu=str(serving["gpu"]),
                max_model_len=int(serving["max_model_len"]),
                args=tuple(str(arg) for arg in serving["args"]),
                generation={str(k): float(v) for k, v in serving["generation"].items()},
            ),
            rollout=Rollout(float(rollout["budget_s"]), str(rollout["jail"]), str(rollout["runtime"])),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise PinsError(f"pins.toml: {type(error).__name__}: {error}") from error
    if len(pins.bh02.sha) != 40:
        raise PinsError("pins.toml: bh02.sha must be a full 40-character commit, so the image is reproducible")
    if pins.rollout.runtime not in _RUNTIMES:
        raise PinsError(f"pins.toml: rollout.runtime must be one of {sorted(_RUNTIMES)}, not {pins.rollout.runtime!r}")
    if pins.rollout.jail not in _JAILS:
        raise PinsError(f"pins.toml: rollout.jail must be one of {sorted(_JAILS)}, not {pins.rollout.jail!r}")
    return pins


def load(path: Path = PINS) -> Pins:
    """The pins in `path`.

    Raises:
        PinsError: The file is not valid TOML, or `parsed` refuses it.
    """
    try:
        return parsed(tomllib.loads(path.read_text(encoding="utf-8")))
    except tomllib.TOMLDecodeError as error:
        raise PinsError(f"{path}: {error}") from error


def serving_hash(pins: Pins) -> str:
    """The model's name to the gate: a hash of every pin that decides what the server answers."""
    decided = {
        "repo": pins.model.repo,
        "revision": pins.model.revision,
        "served_name": pins.model.served_name,
        "temperature": pins.model.temperature,
        "vllm": pins.serving.vllm,
        "max_model_len": pins.serving.max_model_len,
        "args": list(pins.serving.args),
        "generation": dict(sorted(pins.serving.generation.items())),
    }
    return "srv-" + digest(json.dumps(decided, sort_keys=True).encode())[:16]
