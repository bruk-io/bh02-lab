import dataclasses
import tomllib

import pytest

from bh02_lab.pins import PINS, PinsError, load, parsed, serving_hash


def test_the_repository_pins_load():
    pins = load()
    assert len(pins.bh02.sha) == 40 and pins.model.repo == "ornith-ai/Ornith-1.5-9B"


def test_the_serving_hash_moves_with_every_serving_pin():
    pins = load()
    base = serving_hash(pins)
    assert base == serving_hash(load())
    changed = [
        dataclasses.replace(pins, model=dataclasses.replace(pins.model, revision="other")),
        dataclasses.replace(pins, model=dataclasses.replace(pins.model, temperature=1.0)),
        dataclasses.replace(pins, serving=dataclasses.replace(pins.serving, vllm="0.20.0")),
        dataclasses.replace(pins, serving=dataclasses.replace(pins.serving, args=("--x",))),
        dataclasses.replace(pins, serving=dataclasses.replace(pins.serving, generation={"top_p": 0.5})),
    ]
    assert all(serving_hash(other) != base for other in changed)


def test_the_gpu_is_not_part_of_the_model():
    pins = load()
    other = dataclasses.replace(pins, serving=dataclasses.replace(pins.serving, gpu="H100"))
    assert serving_hash(other) == serving_hash(pins)


def test_a_short_sha_is_refused():
    data = tomllib.loads(PINS.read_text())
    data["bh02"]["sha"] = "dc4bd33"
    with pytest.raises(PinsError, match="40-character"):
        parsed(data)


def test_a_missing_table_is_refused():
    data = tomllib.loads(PINS.read_text())
    del data["serving"]
    with pytest.raises(PinsError, match="serving"):
        parsed(data)


def test_an_unknown_jail_is_refused():
    data = tomllib.loads(PINS.read_text())
    data["rollout"]["jail"] = "none"
    with pytest.raises(PinsError, match="rollout.jail"):
        parsed(data)
