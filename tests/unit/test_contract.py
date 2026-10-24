import pytest

from bh02_lab.contract import ContractError, Rollout, RolloutRequest, Verdict, genome_id

REQUEST = RolloutRequest("im-abc", frozenset({"p2", "p1"}), "srv-1", "paginate", 3)


def test_a_request_survives_the_wire() -> None:
    assert RolloutRequest.from_json(REQUEST.to_json()) == REQUEST


def test_the_genome_crosses_sorted() -> None:
    assert REQUEST.to_json()["genome"] == ["p1", "p2"]


def test_a_rollout_survives_the_wire() -> None:
    rollout = Rollout(REQUEST, "run1", "snap", "trans", "replied")
    assert Rollout.from_json(rollout.to_json()) == rollout


def test_a_verdict_survives_the_wire_and_has_its_key() -> None:
    verdict = Verdict("snap", "paginate", True, "judge-1", "6/6")
    assert Verdict.from_json(verdict.to_json()) == verdict
    assert verdict.key == "judge-1/paginate/snap"


@pytest.mark.parametrize(
    ("field", "value"),
    [("seed", -1), ("seed", True), ("seed", "3"), ("task_id", ""), ("genome", ["ok", 1]), ("model", None)],
)
def test_a_bad_field_is_refused(field: str, value: object) -> None:
    data = REQUEST.to_json() | {field: value}
    with pytest.raises(ContractError, match=field):
        RolloutRequest.from_json(data)


def test_a_verdict_without_a_boolean_is_refused() -> None:
    with pytest.raises(ContractError, match="passed"):
        Verdict.from_json({"snapshot": "s", "task_id": "t", "passed": 1, "judge_sha": "j"})


def test_the_empty_genome_is_the_incumbent() -> None:
    assert genome_id(frozenset()) == "incumbent"
