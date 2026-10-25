"""Ornith served by vLLM on a Modal GPU: an OpenAI-compatible endpoint behind a key, scaled to
zero when idle. Everything that decides its answers comes from pins.toml, and the same pins make
the serving hash every rollout records.

Deploy: `modal deploy -m bh02_lab.modal_apps.serve`. The URL it prints is the rollout's
`base_url` (with `/v1`).
"""

import json
import subprocess

import modal

from bh02_lab.modal_apps.common import APPS, INFERENCE_SECRET, lab_image, pins
from bh02_lab.pins import Pins

__all__ = ["app", "command", "serve"]

PORT = 8000
_PINS = pins()

app = modal.App(APPS["serve"])
weights = modal.Volume.from_name("bh02-lab-hf-cache", create_if_missing=True)
compile_cache = modal.Volume.from_name("bh02-lab-vllm-cache", create_if_missing=True)

image = lab_image(f"vllm=={_PINS.serving.vllm}", "huggingface_hub", env={"HF_HOME": "/cache/hf"})


def command(pinned: Pins) -> list[str]:
    """The vLLM command for the pins; the key is read from the environment at start."""
    model = pinned.model
    return [
        "vllm", "serve", model.repo,
        "--revision", model.revision,
        "--tokenizer-revision", model.revision,
        "--served-model-name", model.served_name,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--max-model-len", str(pinned.serving.max_model_len),
        "--override-generation-config", json.dumps(dict(pinned.serving.generation), sort_keys=True),
        *pinned.serving.args,
    ]  # fmt: skip


@app.function(
    image=image,
    gpu=_PINS.serving.gpu,
    volumes={"/cache/hf": weights, "/root/.cache/vllm": compile_cache},
    secrets=[modal.Secret.from_name(INFERENCE_SECRET)],
    scaledown_window=10 * 60,
    timeout=24 * 60 * 60,
    max_containers=1,
)
@modal.concurrent(max_inputs=64)
@modal.web_server(port=PORT, startup_timeout=20 * 60)
def serve() -> None:
    """Start vLLM; Modal routes the endpoint's requests to it once the port answers."""
    import os

    subprocess.Popen([*command(_PINS), "--api-key", os.environ["ORNITH_KEY"]])
