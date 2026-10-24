"""The row: `use = "headless:ui"` in place of bh-02's `tui:ui`, binding the same three keys."""

from pathlib import Path

from cordis import Effects, bind, component, enter
from headless_cordis_plugin.headless import Frame, HeadlessConfig, Recorder, Script, log

__all__ = ["ui"]


@component(provides=("input", "output", "frame"))
async def ui(*, config: HeadlessConfig) -> Effects:
    """Fills a `ui` row with no terminal: sends the task in `config.task` once, records what bh-02
    shows and asks in `config.events`, and answers every question by `config.confirm`. Depends on
    nothing but its config, as `tui:ui` does, so no reload elsewhere restarts it."""
    records = yield enter(log(config.events))
    yield bind("input", Script(Path(config.task), config.budget_s, records))
    yield bind("output", Recorder(records, config.confirm))
    yield bind("frame", Frame(records))
