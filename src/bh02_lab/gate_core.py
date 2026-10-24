"""The gate's decisions, apart from Modal and HTTP: what a battery run asks for, and what its
verdicts add up to.

A rollout that failed for a reason of the lab's own (the sandbox didn't start, a function raised)
is not a failed task: it is left out of the score and counted, and a run with any is reported as
incomplete, so infrastructure never reads as the model getting worse.

Pure.
"""

import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from bh02_lab.contract import ContractError, RolloutRequest, Verdict, genome_id
from bh02_lab.stats import noise_band

__all__ = ["BatteryAsk", "asked", "requests_for", "summary"]

_HASH = re.compile(r"[0-9a-f]{64}")
MAX_K = 20
BAND_SEED = 0  # the bootstrap's own seed: the same verdicts always give the same band


@dataclass(frozen=True, slots=True)
class BatteryAsk:
    """A battery run as asked over the wire."""

    genome: frozenset[str]
    k: int
    battery: str


def asked(body: Mapping[str, Any]) -> BatteryAsk:
    """The battery run a request body asks for.

    Raises:
        ContractError: The genome is not a list of plugin hashes, `k` is not between 1 and
            `MAX_K`, or the battery is not a plain name.
    """
    genome = body.get("genome", [])
    if not isinstance(genome, list) or not all(isinstance(h, str) and _HASH.fullmatch(h) for h in genome):
        raise ContractError("genome must be a list of plugin hashes (64 lowercase hex characters each)")
    k = body.get("k", 5)
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= MAX_K:
        raise ContractError(f"k must be an integer from 1 to {MAX_K}")
    battery = body.get("battery", "core")
    if not isinstance(battery, str) or not re.fullmatch(r"[a-z0-9-]+", battery):
        raise ContractError("battery must be a battery's name")
    return BatteryAsk(frozenset(genome), k, battery)


def requests_for(ask: BatteryAsk, task_ids: Sequence[str], image_digest: str, model: str) -> list[RolloutRequest]:
    """One rollout per task and seed, in a stable order."""
    return [
        RolloutRequest(image_digest, ask.genome, model, task_id, seed)
        for task_id in sorted(task_ids)
        for seed in range(ask.k)
    ]


def summary(
    ask: BatteryAsk,
    requests: Sequence[RolloutRequest],
    verdicts: Sequence[Verdict | BaseException],
    pins: Mapping[str, str],
) -> dict[str, Any]:
    """What a finished battery run reports: the score with its band, each task's rate, every
    rollout's verdict, and the rollouts the lab itself failed to run."""
    results: dict[tuple[str, int], bool] = {}
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for request, verdict in zip(requests, verdicts, strict=True):
        if isinstance(verdict, BaseException):
            errors.append(
                {"task_id": request.task_id, "seed": request.seed, "error": f"{type(verdict).__name__}: {verdict}"}
            )
            continue
        results[request.task_id, request.seed] = verdict.passed
        rows.append({"task_id": request.task_id, "seed": request.seed, **verdict.to_json()})
    report: dict[str, Any] = {
        "genome": genome_id(ask.genome),
        "battery": ask.battery,
        "k": ask.k,
        "pins": dict(pins),
        "complete": not errors,
        "errors": errors,
        "verdicts": rows,
    }
    if results:
        band = noise_band(results, random.Random(BAND_SEED))
        report |= {
            "score": band.estimate,
            "band": [band.low, band.high],
            "tasks": {
                task: sum(passed for (t, _), passed in results.items() if t == task)
                / sum(1 for (t, _) in results if t == task)
                for task in sorted({t for t, _ in results})
            },
        }
    return report
