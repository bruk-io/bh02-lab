"""The headless values with no bh-02 and no cordis runtime: what each records and answers."""

import asyncio
import io
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from headless_cordis_plugin import Frame, HeadlessConfig, Log, Recorder, Script


def _records(buffer: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(text) for text in buffer.getvalue().splitlines()]


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_config_refuses_an_unknown_policy() -> None:
    with pytest.raises(ValueError, match="confirm must be one of"):
        HeadlessConfig(task="t", events="e", confirm="ask")


def test_config_refuses_a_budget_that_is_not_positive() -> None:
    with pytest.raises(ValueError, match="budget_s must be positive"):
        HeadlessConfig(task="t", events="e", budget_s=0)


async def test_the_task_is_read_once_then_there_is_no_more_input(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("fix the bug")
    buffer = io.StringIO()
    clock = _Clock()
    script = Script(task, 60.0, Log(buffer, clock), clock, clock.sleep)
    assert await script.read() == "fix the bug"
    assert await script.read() is None
    kinds = [(r["kind"], r.get("reason")) for r in _records(buffer)]
    assert kinds == [("task", None), ("end", "replied")]


async def test_a_reply_past_its_budget_is_interrupted_and_ends_as_budget(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("go")
    buffer = io.StringIO()
    clock = _Clock()
    script = Script(task, 30.0, Log(buffer, clock), clock, clock.sleep)
    await script.read()
    clock.now += 10
    await script.interrupted()
    assert clock.slept == [20.0]
    assert await script.read() is None
    assert _records(buffer)[-1]["reason"] == "budget"


async def test_interrupted_waits_forever_before_the_task_is_read(tmp_path: Path) -> None:
    clock = _Clock()
    script = Script(tmp_path / "unread", 1.0, Log(io.StringIO(), clock), clock, clock.sleep)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(script.interrupted(), 0.05)


async def _events() -> AsyncIterator[dict[str, object]]:
    yield {"type": "text", "text": "hi"}
    yield {"type": "stop", "reason": "end_turn"}


async def test_the_recorder_keeps_every_event_and_answers_by_policy() -> None:
    buffer = io.StringIO()
    denying = Recorder(Log(buffer, lambda: 1.0), "deny")
    await denying.show(_events())
    await denying.notice("model restarting")
    assert await denying.confirm({"tool": "python"}) is False
    assert await Recorder(Log(buffer, lambda: 1.0), "allow").confirm("run?") is True
    records = _records(buffer)
    assert [r["kind"] for r in records] == ["event", "event", "notice", "confirm", "confirm"]
    assert records[0]["event"] == {"type": "text", "text": "hi"}
    assert records[3] == {"kind": "confirm", "at": 1.0, "question": {"tool": "python"}, "answer": False}


def test_a_value_json_cannot_hold_is_kept_as_its_repr() -> None:
    buffer = io.StringIO()
    Log(buffer, lambda: 1.0).write("event", {"event": {"path": Path("/x")}})
    assert _records(buffer)[0]["event"] == {"path": "PosixPath('/x')"}


def test_the_frame_records_status_and_hands_back_removers() -> None:
    buffer = io.StringIO()
    frame = Frame(Log(buffer, lambda: 1.0))
    remove = frame.status("model", "ornith", "orn")
    remove()
    frame.commands(lambda: ())()
    assert _records(buffer) == [{"kind": "status", "at": 1.0, "field": "model", "text": "ornith"}]
