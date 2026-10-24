import random

import pytest

from bh02_lab.stats import noise_band, paired_diff, score


def _results(rates: dict[str, list[bool]]) -> dict[tuple[str, int], bool]:
    return {(task, seed): passed for task, outcomes in rates.items() for seed, passed in enumerate(outcomes)}


def test_each_task_counts_once_whatever_its_seeds():
    results = _results({"a": [True] * 9 + [False], "b": [False]})
    assert score(results) == pytest.approx(0.45)


def test_no_results_cannot_be_scored():
    with pytest.raises(ValueError, match="no results"):
        score({})


def test_a_noiseless_run_has_a_zero_width_band():
    band = noise_band(_results({"a": [True] * 5, "b": [False] * 5}), random.Random(0))
    assert (band.estimate, band.low, band.high) == (0.5, 0.5, 0.5)


def test_the_band_holds_the_estimate_and_is_reproducible():
    results = _results({"a": [True, False, True, True, False], "b": [False, False, True, False, True]})
    first = noise_band(results, random.Random(7))
    assert first == noise_band(results, random.Random(7))
    assert first.low <= first.estimate <= first.high and first.width > 0


def test_more_seeds_narrow_the_band():
    few = noise_band(_results({"a": [True, False] * 2}), random.Random(1))
    many = noise_band(_results({"a": [True, False] * 50}), random.Random(1))
    assert many.width < few.width


def test_paired_diff_of_identical_runs_is_zero():
    results = _results({"a": [True, False, True], "b": [False, True, True]})
    band = paired_diff(results, results, random.Random(0))
    assert (band.estimate, band.low, band.high) == (0.0, 0.0, 0.0)


def test_paired_diff_sees_a_clear_win():
    candidate = _results({task: [True] * 5 for task in "abcdef"})
    incumbent = _results({task: [False] * 5 for task in "abcdef"})
    assert paired_diff(candidate, incumbent, random.Random(0)).low == 1.0


def test_paired_diff_needs_shared_keys():
    with pytest.raises(ValueError, match="share no"):
        paired_diff({("a", 0): True}, {("b", 0): True}, random.Random(0))


def test_a_level_outside_zero_and_one_is_refused():
    with pytest.raises(ValueError, match="level"):
        noise_band({("a", 0): True}, random.Random(0), level=1.5)
