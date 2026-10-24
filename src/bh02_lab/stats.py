"""How sure a battery score is: the score, the band its noise allows, and the paired difference
between two genomes or models run on the same tasks and seeds.

The unit is a rollout's verdict, keyed by `(task_id, seed)`. Seeds are resampled within each task
(a stratified bootstrap), so a task with more seeds does not weigh more than its share. Randomness
comes in as an `rng`, so a band is reproducible.

Pure.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Band", "noise_band", "paired_diff", "score"]

type Results = Mapping[tuple[str, int], bool]


@dataclass(frozen=True, slots=True)
class Band:
    """A point estimate and its percentile interval."""

    estimate: float
    low: float
    high: float

    @property
    def width(self) -> float:
        return self.high - self.low


def score(results: Results) -> float:
    """The mean of the per-task pass rates: each task counts once, whatever its seeds.

    >>> score({("a", 0): True, ("a", 1): False, ("b", 0): True})
    0.75

    Raises:
        ValueError: There are no results.
    """
    rates = _rates(_by_task(results))
    if not rates:
        raise ValueError("no results to score")
    return sum(rates) / len(rates)


def noise_band(results: Results, rng: random.Random, draws: int = 2000, level: float = 0.95) -> Band:
    """The score and its bootstrap interval, resampling seeds within each task.

    Raises:
        ValueError: There are no results, or `level` is not between 0 and 1.
    """
    by_task = _by_task(results)
    estimate = score(results)
    samples = sorted(
        sum(_mean([outcomes[rng.randrange(len(outcomes))] for _ in outcomes]) for outcomes in by_task.values())
        / len(by_task)
        for _ in range(draws)
    )
    return Band(estimate, *_percentiles(samples, level))


def paired_diff(
    candidate: Results, incumbent: Results, rng: random.Random, draws: int = 2000, level: float = 0.95
) -> Band:
    """The candidate's score minus the incumbent's, over the keys both ran, with a bootstrap
    interval that resamples tasks and keeps each task's pairs together.

    Raises:
        ValueError: The two share no `(task_id, seed)`, or `level` is not between 0 and 1.
    """
    shared = sorted(candidate.keys() & incumbent.keys())
    if not shared:
        raise ValueError("the two runs share no (task, seed); a paired difference needs the same keys")
    tasks = sorted({task for task, _ in shared})
    diffs = {
        task: [float(candidate[key]) - float(incumbent[key]) for key in shared if key[0] == task] for task in tasks
    }
    estimate = _mean([_mean(values) for values in diffs.values()])
    samples = sorted(
        _mean([_mean(diffs[tasks[rng.randrange(len(tasks))]]) for _ in tasks]) for _ in range(draws)
    )
    return Band(estimate, *_percentiles(samples, level))


def _by_task(results: Results) -> dict[str, list[float]]:
    by_task: dict[str, list[float]] = {}
    for (task, _), passed in sorted(results.items()):
        by_task.setdefault(task, []).append(float(passed))
    return by_task


def _rates(by_task: Mapping[str, Sequence[float]]) -> list[float]:
    return [_mean(outcomes) for outcomes in by_task.values()]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _percentiles(samples: Sequence[float], level: float) -> tuple[float, float]:
    if not 0 < level < 1:
        raise ValueError(f"level must be between 0 and 1, not {level}")
    tail = (1 - level) / 2
    low = samples[int(tail * (len(samples) - 1))]
    high = samples[int((1 - tail) * (len(samples) - 1))]
    return low, high
