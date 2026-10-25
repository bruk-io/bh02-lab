"""Every image the lab defines can be built: Modal refuses a build step on top of local files
added without `copy=True`, and says so only at deploy time. This finds it before then.

It reads Modal's own image objects (`_Image`, `_deps`, `_rep`), which are not Modal's public API:
if a Modal upgrade moves them, this test fails loudly rather than passing for nothing.
"""

import importlib

import modal
import pytest
from modal._image import _Image
from modal._utils.async_utils import synchronizer

_LOCAL_FILES = "Image(local files)"  # how Modal labels a layer of files added at container start
MODULES = ("serve", "rollout", "judge", "gate", "train_proof")


def _images() -> list[tuple[str, modal.Image]]:
    found = []
    for name in MODULES:
        module = importlib.import_module(f"bh02_lab.modal_apps.{name}")
        found += [(f"{name}.{attr}", value) for attr, value in vars(module).items() if isinstance(value, modal.Image)]
    return found


def _raw(image: modal.Image) -> _Image:
    raw: _Image = synchronizer._translate_in(image)  # type: ignore[no-untyped-call]
    return raw


def _steps_after_local_files(image: _Image) -> list[str]:
    found = []
    for base in image._deps() if image._deps else ():
        if isinstance(base, _Image):
            if image._rep != _LOCAL_FILES and base._rep == _LOCAL_FILES:
                found.append(image._rep)
            found += _steps_after_local_files(base)
    return found


def test_every_app_defines_an_image() -> None:
    names = {name.split(".")[0] for name, _ in _images()}
    assert names == set(MODULES)


@pytest.mark.parametrize(("name", "image"), _images(), ids=[name for name, _ in _images()])
def test_no_build_step_follows_local_files(name: str, image: modal.Image) -> None:
    raw = _raw(image)
    assert isinstance(raw, _Image) and hasattr(raw, "_rep"), "Modal's image internals moved; update this test"
    assert _steps_after_local_files(raw) == [], f"{name}: a build step comes after add_local_* without copy=True"


def test_the_check_catches_the_mistake() -> None:
    from pathlib import Path

    wrong = modal.Image.debian_slim().add_local_file(Path(__file__), "/a").env({"A": "1"})
    assert _steps_after_local_files(_raw(wrong))
