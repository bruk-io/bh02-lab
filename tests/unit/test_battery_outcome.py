from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from bh02_lab.battery import Case, observed, outcome, public_steps

CASES = (
    Case("adds", ({"call": "m:add", "args": [1, 2], "expect": {"returns": 3}},)),
    Case(
        "raises then prints",
        (
            {"call": "m:bad", "expect": {"raises": "ValueError"}},
            {"call": "m:show", "capture_stdout": True, "expect": {"stdout_json": {"a": 1}, "returns": 0}},
        ),
    ),
    Case("floats", ({"call": "m:f", "expect": {"approx": {"x": 0.3}}},)),
)
GOOD: dict[int, Sequence[Mapping[str, Any]]] = {
    0: [{"i": 0, "returned": 3}],
    1: [{"i": 0, "raised": ["ValueError", "Exception"]}, {"i": 1, "returned": 0, "stdout": '{"a": 1}\n'}],
    2: [{"i": 0, "returned": {"x": 0.1 + 0.2}}],
}


def test_every_case_matching_passes() -> None:
    assert outcome(CASES, GOOD) == outcome(CASES, GOOD) and outcome(CASES, GOOD).passed


@pytest.mark.parametrize(
    ("case", "records"),
    [
        (0, [{"i": 0, "returned": 4}]),
        (0, [{"i": 0, "raised": ["TypeError"]}]),
        (0, []),
        (1, [{"i": 0, "raised": ["KeyError"]}, GOOD[1][1]]),
        (1, [GOOD[1][0], {"i": 1, "returned": 0, "stdout": "not json"}]),
        (2, [{"i": 0, "returned": {"x": 0.31}}]),
        (2, [{"i": 0, "returned": {"x": 0.3, "y": 1}}]),
    ],
)
def test_a_wrong_or_missing_observation_fails(case: int, records: list[Mapping[str, Any]]) -> None:
    result = outcome(CASES, GOOD | {case: records})
    assert not result.passed and CASES[case].name in result.detail


def test_a_missing_case_fails() -> None:
    assert not outcome(CASES, {0: GOOD[0], 1: GOOD[1]}).passed


def test_no_cases_never_passes() -> None:
    assert not outcome((), {}).passed


def test_a_returned_true_is_not_one() -> None:
    case = (Case("bool", ({"call": "m:f", "expect": {"approx": 1}},)),)
    assert not outcome(case, {0: [{"i": 0, "returned": True}]}).passed


def test_only_the_first_report_of_a_case_counts() -> None:
    text = 'CASE 0 [{"i": 0, "returned": 4}]\nCASE 0 [{"i": 0, "returned": 3}]\n'
    assert not outcome(CASES[:1], observed(text)).passed


def test_the_sandbox_steps_lose_their_expectations() -> None:
    assert public_steps(CASES[:1]) == [[{"call": "m:add", "args": [1, 2]}]]
