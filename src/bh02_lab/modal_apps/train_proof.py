"""The training proof (TASK-0001.09): show that fine-tuning works on Modal before anything leans
on it. Unsloth, bf16 LoRA (Unsloth advises against QLoRA for Qwen3.5), r=16 over every linear
module, loss on the responses only, 60 steps, on a set that teaches one fact (`bh02_lab.proof`).
Then the same chat template asks a held-out question with and without the adapter.

It passes when the adapter answers with the codeword and the base model doesn't. The adapter,
the samples and the package versions go to the `bh02-lab-adapters` volume.

Run: `modal run -m bh02_lab.modal_apps.train_proof`.
"""

import json
from typing import Any

import modal

from bh02_lab.modal_apps.common import APPS, lab_image
from bh02_lab.proof import Samples, conversations, held_out, passed

__all__ = ["app", "main", "proof"]

_MODEL = "Qwen/Qwen3.5-0.8B"
_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
_USER, _ASSISTANT = "<|im_start|>user\n", "<|im_start|>assistant\n"  # Qwen's ChatML turns

app = modal.App(APPS["train"])
adapters = modal.Volume.from_name("bh02-lab-adapters", create_if_missing=True)
weights = modal.Volume.from_name("bh02-lab-hf-cache", create_if_missing=True)
image = lab_image("unsloth", "unsloth_zoo", env={"HF_HOME": "/cache/hf"})


def _answer(model: Any, tokenizer: Any, question: str) -> str:
    text = getattr(tokenizer, "tokenizer", tokenizer)
    prompt = text.apply_chat_template(
        [{"role": "user", "content": question}], add_generation_prompt=True, enable_thinking=False, tokenize=False
    )
    inputs = text(prompt, return_tensors="pt").to("cuda")
    output = model.generate(**inputs, max_new_tokens=48, do_sample=False)
    return str(text.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)).strip()


@app.function(image=image, gpu="L40S", volumes={"/adapters": adapters, "/cache/hf": weights}, timeout=60 * 60)
def proof(model_name: str = _MODEL, steps: int = 60) -> dict[str, Any]:
    """Train the adapter, sample with and without it, and keep what happened."""
    import importlib.metadata

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name, max_seq_length=1024, load_in_4bit=False, load_in_16bit=True, full_finetuning=False
    )
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=16, lora_dropout=0, bias="none", target_modules=_TARGETS,
        use_gradient_checkpointing="unsloth", random_state=3407,
    )  # fmt: skip
    text = getattr(tokenizer, "tokenizer", tokenizer)
    rows = [text.apply_chat_template(c, tokenize=False, enable_thinking=False) for c in conversations()]
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=Dataset.from_dict({"text": rows}),
        args=SFTConfig(
            dataset_text_field="text", per_device_train_batch_size=1, gradient_accumulation_steps=4,
            warmup_steps=5, max_steps=steps, learning_rate=2e-4, logging_steps=1, optim="adamw_8bit",
            weight_decay=0.01, lr_scheduler_type="linear", seed=3407, output_dir="/tmp/proof",
            report_to="none", dataset_num_proc=1,
        ),
    )  # fmt: skip
    trainer = train_on_responses_only(trainer, instruction_part=_USER, response_part=_ASSISTANT)
    trained = trainer.train()
    FastLanguageModel.for_inference(model)
    question = held_out()
    tuned = _answer(model, tokenizer, question)
    with model.disable_adapter():
        base = _answer(model, tokenizer, question)
    samples = Samples(question, base, tuned)
    model.save_pretrained("/adapters/proof")
    tokenizer.save_pretrained("/adapters/proof")
    report = {
        "passed": passed(samples),
        "model": model_name,
        "steps": steps,
        "final_loss": trained.training_loss,
        "samples": {"question": question, "base": base, "tuned": tuned},
        "template_example": rows[0],
        "versions": {name: importlib.metadata.version(name) for name in ("unsloth", "transformers", "trl", "torch")},
    }
    with open("/adapters/proof/report.json", "w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    adapters.commit()
    return report


@app.local_entrypoint()
def main(steps: int = 60) -> None:
    """Run the proof and print its report.

    Raises:
        SystemExit: The proof did not pass (exit 1).
    """
    report = proof.remote(steps=steps)
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)
