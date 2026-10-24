"""bh-02 with no terminal: the three values the `ui` row binds (`input`, `output` and `frame`,
bh-02's CONTRACTS.md), made for a rollout instead of a person.

`input` hands the chat the task's message once, then says there is no more input, so the chat row
ends and the shell exits once the reply is done. A reply still running when the budget runs out is
interrupted, as Ctrl-C would. `output` records every event the chat shows, every notice and every
question it is asked, one JSON object per line, and answers a question by the run's policy: a
rollout has nobody to ask. `frame` takes status fields and commands and draws nothing, so the rows
that push to it (and the model's extensions) run as they do under the TUI.

This module does not import cordis (bh-02's `cordis-in-wiring-only`); `wiring.py` binds it. The
pure parts are `line` and `answered`; `Log` is the one writer.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TextIO

__all__ = ["Frame", "HeadlessConfig", "Log", "Recorder", "Script", "answered", "line", "log"]

_POLICIES: Final = frozenset({"deny", "allow"})


@dataclass(frozen=True, slots=True)
class HeadlessConfig:
    """The headless row's config. `task`: the file whose text is the one message sent. `events`:
    the JSONL file every record is appended to. `budget_s`: how long the reply may run before it is
    interrupted, from the moment the message is read. `confirm`: `deny` answers every question no,
    `allow` answers yes (for a run whose sandbox is the only jail, `--no-jail`, where every input
    asks)."""

    task: str
    events: str
    budget_s: float = 1800.0
    confirm: str = "deny"

    def __post_init__(self) -> None:
        if self.confirm not in _POLICIES:
            raise ValueError(f"confirm must be one of {sorted(_POLICIES)}, not {self.confirm!r}")
        if self.budget_s <= 0:
            raise ValueError(f"budget_s must be positive, not {self.budget_s!r}")


def line(kind: str, at: float, payload: Mapping[str, Any]) -> str:
    """One record as the line written for it: `kind`, `at` (seconds since the epoch) and the
    payload's fields, as JSON. A value JSON can't hold is written as its repr, so a record is
    never lost to its shape.

    >>> line("notice", 1.0, {"message": "hi"})
    '{"kind": "notice", "at": 1.0, "message": "hi"}'
    """
    return json.dumps({"kind": kind, "at": at, **payload}, default=repr, ensure_ascii=False)


def answered(policy: str, question: object) -> bool:
    """The answer to a question under `policy`; the question does not change it, by design: a
    rollout's answers are part of what it is pinned to, never a judgement made mid-run.

    >>> answered("deny", "run this?"), answered("allow", "run this?")
    (False, True)
    """
    return policy == "allow"


class Log:
    """Appends records to one open file, a line each, flushed at once so a crash keeps them."""

    def __init__(self, file: TextIO, clock: Callable[[], float] = time.time) -> None:
        self._file = file
        self._clock = clock

    def write(self, kind: str, payload: Mapping[str, Any]) -> None:
        self._file.write(line(kind, self._clock(), payload) + "\n")
        self._file.flush()


@asynccontextmanager
async def log(path: str) -> AsyncIterator[Log]:
    """The run's log, open for as long as the row is up."""
    with Path(path).open("a", encoding="utf-8") as file:
        yield Log(file)


class Script:
    """`input` for one task: the message once, then no more input. The budget starts when the
    message is read; `interrupted` returns when it runs out."""

    def __init__(
        self,
        task: Path,
        budget_s: float,
        log: Log,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._task = task
        self._budget_s = budget_s
        self._log = log
        self._clock = clock
        self._sleep = sleep
        self._deadline: float | None = None
        self._closed = asyncio.Event()

    async def read(self) -> str | None:
        if self._deadline is None:
            message = self._task.read_text(encoding="utf-8")
            self._deadline = self._clock() + self._budget_s
            self._log.write("task", {"message": message})
            return message
        self._log.write("end", {"reason": "replied" if self._clock() < self._deadline else "budget"})
        return None

    async def interrupted(self) -> None:
        if self._deadline is None:  # nothing read yet: nothing to interrupt
            await self._closed.wait()
        else:
            await self._sleep(max(0.0, self._deadline - self._clock()))
            self._log.write("budget", {"budget_s": self._budget_s})

    async def closed(self) -> None:
        await self._closed.wait()  # a rollout never leaves; the chat ends when read says so


class Recorder:
    """`output`: records what bh-02 shows and asks, and answers by the policy."""

    def __init__(self, log: Log, policy: str) -> None:
        self._log = log
        self._policy = policy

    async def show(self, events: AsyncIterator[Mapping[str, Any]]) -> None:
        async for event in events:
            self._log.write("event", {"event": event})

    async def notice(self, message: str) -> None:
        self._log.write("notice", {"message": message})

    async def confirm(self, question: object) -> bool:
        answer = answered(self._policy, question)
        self._log.write("confirm", {"question": question, "answer": answer})
        return answer


class Frame:
    """`frame` with nothing to draw: a status field is recorded, a command set is accepted, and
    each hands back a remover that does nothing more."""

    def __init__(self, log: Log) -> None:
        self._log = log

    def status(self, field: str, text: str, *shorter: str) -> Callable[[], None]:
        self._log.write("status", {"field": field, "text": text})
        return _nothing

    def commands(self, specs: Callable[[], Sequence[Mapping[str, Any]]]) -> Callable[[], None]:
        return _nothing


def _nothing() -> None:
    return None
