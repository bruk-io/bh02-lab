# CLAUDE.md

The bh-02 lab: rollouts of bh-02 on Modal, a judge bh-02 can't touch, a gate the lab lead calls. `README.md` is the map and the first-run steps; `backlog/` is the work.

## Commands

```
uv sync --extra modal
BH02_CHECKOUT=<bh-02 at the pinned SHA> scripts/check   # the one definition of green
scripts/bh02-checkout DIR                                # make that checkout
scripts/arch-check                                       # import boundaries (pypeeker, pinned as bh-02 pins it)
uv run bh02-lab --help                                   # the lab lead's CLI
```

## Rules that hold the trust boundaries

- **The judge never imports bh-02**, the headless row, cordis or the rollout side, and its images hold none (`tests/unit/test_judge_isolation.py`). A bh-02 change must never change how a snapshot is judged.
- **Hidden checks never reach a rollout**: the runner's and gate's images copy `battery/` without `check/` and `reference/`; only the judge's images hold `check/`.
- **Credentials stay where they belong**: the inference key in the serving app and the rollout sandbox (bh-02's `local.env`, which its jail keeps from the model's code); the gate token in the gate and in the lead's vault; Modal's and Anthropic's own credentials nowhere in a sandbox or the lead's environment.
- **A rollout is pinned by what was built, not what it says**: the runner refuses a request whose image digest or serving hash is not the pinned one, and hashes and re-packs the workspace itself.
- **Expected values never enter a sandbox**: the check sandbox gets `battery.public_steps` (no `expect`), and `battery.outcome` compares outside it. Never weaken that rule: every step with an expected value observed and matching, every case.
- **The lab's failures raise, the model's are verdicts, and the line between them is where the model's code starts**: before it (the `prepare` step, serving health, getting the snapshot into the judge's sandbox) a failure raises and the gate leaves it out of the score; after it, everything is the model's (`rollout_core.settled`, the judge's `_run_cases`), because the model may have caused it. Never move work across that line.
- **Each judge case runs as `nobody` under limits** (`sandbox/cases.py`), and its report goes to a file only root can write.

## Code

- Python 3.12 is the lab's floor, not 3.15: its code runs in Modal containers and in the lead's cloud sandbox (`backlog/decisions/`). `headless/` is 3.15, because it runs inside bh-02.
- Pure core, thin shells: every module under `src/bh02_lab/` but `modal_apps/`, `cli` and `gate_api` is pure or reads only files; `[tool.pypeeker.import-boundaries]` in `pyproject.toml` says what may import what, and a new module needs a row there.
- `src/bh02_lab/sandbox/entry.py` runs in the untrusted sandbox with the standard library only; it imports nothing of the lab.
- Tests: `tests/unit` (pure, no doubles), `tests/functional` (fakes at the boundary: a fake battery runner behind the real gate API), `tests/e2e` (the real bh-02 checkout). `headless/tests` run in bh-02's own environment.
- Canadian spelling, plain hyphens, no em dashes.
