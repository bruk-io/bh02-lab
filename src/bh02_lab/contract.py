"""What the gate, the rollout runner and the judge agree on, as values, and their wire form.

A rollout is pinned to what it ran, never to what it says it ran: the image by the digest Modal
built, the genome by the hashes of its plugin files, the model by the hash of its serving config.
Its outputs are named by content hash too. The snapshot is evidence; the transcript is training
data and never evidence, so no verdict reads it.

Pure: no I/O, no Modal, no bh-02.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ContractError", "Rollout", "RolloutRequest", "Verdict", "genome_id"]

type Json = Mapping[str, Any]


class ContractError(ValueError):
    """A wire value that is not the shape the contract says."""


@dataclass(frozen=True, slots=True)
class RolloutRequest:
    """One rollout to run: the image, the genome and the model it runs on, the task, and the
    replicate. `seed` is the replicate's index: bh-02's OpenAI provider sends no sampling seed,
    so replicates differ by the server's sampling, which is the noise the gate measures."""

    image_digest: str
    genome: frozenset[str]
    model: str
    task_id: str
    seed: int

    def to_json(self) -> dict[str, Any]:
        return {
            "image_digest": self.image_digest,
            "genome": sorted(self.genome),
            "model": self.model,
            "task_id": self.task_id,
            "seed": self.seed,
        }

    @classmethod
    def from_json(cls, data: Json) -> "RolloutRequest":
        """The request a wire value holds.

        Raises:
            ContractError: A field is missing or of the wrong type.
        """  # ruff: ignore[docstring-extraneous-exception] - the field readers raise it
        return cls(
            image_digest=_text(data, "image_digest"),
            genome=frozenset(_texts(data, "genome")),
            model=_text(data, "model"),
            task_id=_text(data, "task_id"),
            seed=_integer(data, "seed"),
        )


@dataclass(frozen=True, slots=True)
class Rollout:
    """A finished rollout: its request, its own id (what its event log and bh-02's log are kept
    under), and the content hashes of the workspace it left and of bh-02's transcript. `ended` is
    how it ended: `replied` or `budget` (the headless row's own end), `crashed`, or `oversized`
    (a workspace over the snapshot's limits)."""

    request: RolloutRequest
    run_id: str
    snapshot: str
    transcript: str
    ended: str

    def to_json(self) -> dict[str, Any]:
        return {
            "request": self.request.to_json(),
            "run_id": self.run_id,
            "snapshot": self.snapshot,
            "transcript": self.transcript,
            "ended": self.ended,
        }

    @classmethod
    def from_json(cls, data: Json) -> "Rollout":
        """The rollout a wire value holds.

        Raises:
            ContractError: A field is missing or of the wrong type.
        """
        request = data.get("request")
        if not isinstance(request, Mapping):
            raise ContractError("rollout.request must be an object")
        return cls(
            request=RolloutRequest.from_json(request),
            run_id=_text(data, "run_id"),
            snapshot=_text(data, "snapshot"),
            transcript=_text(data, "transcript"),
            ended=_text(data, "ended"),
        )


@dataclass(frozen=True, slots=True)
class Verdict:
    """The judge's finding on one snapshot for one task, under the judge's own pin."""

    snapshot: str
    task_id: str
    passed: bool
    judge_sha: str
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot,
            "task_id": self.task_id,
            "passed": self.passed,
            "judge_sha": self.judge_sha,
            "detail": self.detail,
        }

    @classmethod
    def from_json(cls, data: Json) -> "Verdict":
        """The verdict a wire value holds.

        Raises:
            ContractError: A field is missing or of the wrong type.
        """
        passed = data.get("passed")
        if not isinstance(passed, bool):
            raise ContractError("verdict.passed must be a boolean")
        detail = data.get("detail", "")
        return cls(
            snapshot=_text(data, "snapshot"),
            task_id=_text(data, "task_id"),
            passed=passed,
            judge_sha=_text(data, "judge_sha"),
            detail=detail if isinstance(detail, str) else "",
        )

    @property
    def key(self) -> str:
        """Where the judge's store keeps it: one verdict per snapshot, task and judge pin."""
        return f"{self.judge_sha}/{self.task_id}/{self.snapshot}"


def genome_id(genome: frozenset[str]) -> str:
    """A genome's name in logs and keys: its plugin hashes, sorted and joined, or `incumbent`
    for the empty genome (the shipped composition and nothing more).

    >>> genome_id(frozenset())
    'incumbent'
    >>> genome_id(frozenset({"b2", "a1"}))
    'a1+b2'
    """
    return "+".join(sorted(genome)) or "incumbent"


def _text(data: Json, name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise ContractError(f"{name} must be a non-empty string")
    return value


def _texts(data: Json, name: str) -> list[str]:
    value = data.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError(f"{name} must be a list of non-empty strings")
    return value


def _integer(data: Json, name: str) -> int:
    value = data.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractError(f"{name} must be a non-negative integer")
    return value
