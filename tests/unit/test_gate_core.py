import pytest

from bh02_lab.contract import ContractError, Verdict
from bh02_lab.gate_core import BatteryAsk, asked, requests_for, summary

PLUGIN = "a" * 64


def test_the_defaults_are_the_incumbent_on_core_at_five_seeds() -> None:
    assert asked({}) == BatteryAsk(frozenset(), 5, "core")


@pytest.mark.parametrize(
    "body",
    [{"genome": ["short"]}, {"genome": "x"}, {"k": 0}, {"k": 21}, {"k": True}, {"battery": "../etc"}],
)
def test_a_bad_ask_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(ContractError):
        asked(body)


def test_one_request_per_task_and_seed_in_a_stable_order() -> None:
    ask = BatteryAsk(frozenset({PLUGIN}), 2, "core")
    requests = requests_for(ask, ["b", "a"], "im-1", "srv-1")
    assert [(r.task_id, r.seed) for r in requests] == [("a", 0), ("a", 1), ("b", 0), ("b", 1)]
    assert all(r.genome == frozenset({PLUGIN}) and r.image_digest == "im-1" for r in requests)


def test_the_summary_scores_and_leaves_lab_failures_out() -> None:
    ask = BatteryAsk(frozenset(), 2, "core")
    requests = requests_for(ask, ["a", "b"], "im-1", "srv-1")
    verdicts: list[Verdict | BaseException] = [
        Verdict("s1", "a", True, "j"),
        Verdict("s2", "a", True, "j"),
        Verdict("s3", "b", False, "j"),
        RuntimeError("sandbox did not start"),
    ]
    report = summary(ask, requests, verdicts, {"image": "im-1"})
    assert report["score"] == pytest.approx(0.5) and report["tasks"] == {"a": 1.0, "b": 0.0}
    assert report["complete"] is False and report["errors"][0]["seed"] == 1
    assert len(report["verdicts"]) == 3 and report["genome"] == "incumbent"


def test_a_run_with_no_verdicts_has_no_score() -> None:
    ask = BatteryAsk(frozenset(), 1, "core")
    requests = requests_for(ask, ["a"], "im", "srv")
    report = summary(ask, requests, [RuntimeError("down")], {})
    assert "score" not in report and report["complete"] is False


def test_one_seed_per_task_gives_a_score_and_no_band() -> None:
    ask = BatteryAsk(frozenset(), 1, "core")
    requests = requests_for(ask, ["a", "b"], "im", "srv")
    report = summary(ask, requests, [Verdict("s1", "a", True, "j"), Verdict("s2", "b", False, "j")], {})
    assert report["score"] == pytest.approx(0.5) and report["band"] is None
