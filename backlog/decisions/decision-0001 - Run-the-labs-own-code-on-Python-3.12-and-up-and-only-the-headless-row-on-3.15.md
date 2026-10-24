---
id: decision-0001
title: >-
  Run the lab's own code on Python 3.12 and up, and only the headless row on
  3.15
date: '2026-10-24T20:55:00Z'
status: accepted
---
## Context

The standing floor for Bruk's projects is Python 3.15. The lab's code runs in three places it doesn't choose: Modal containers (the gate, the judge, the rollout runner, the training proof), whose own runtime documents Python up to 3.13 for `debian_slim`; the lab lead's Managed Agents cloud sandbox, which installs the CLI with pip on whatever Python that sandbox has; and the rollout sandbox, where bh-02 itself needs 3.15. Pragmatism outranks the floor when the floor makes the work harder to ship.

## Decision

- `bh02-lab` (`src/bh02_lab`, the CLI, the Modal apps) requires Python 3.12 and is developed on 3.13 (`.python-version`), the version its Modal images use.
- `headless/` requires 3.15: it is a cordis plugin installed into bh-02's own environment, and is tested there.
- `src/bh02_lab/sandbox/entry.py` is standard library only and runs on the rollout image's system Python, so it is written for 3.12 too.

## Consequences

- No 3.13+ syntax in `src/bh02_lab` (ruff's `target-version` and mypy's `python_version` are 3.12, so a use is a finding).
- The lab and bh-02 never share an environment: `scripts/check` runs the headless tests with the bh-02 checkout's interpreter.
- Revisit when Modal's images and the Managed Agents sandbox offer 3.15.

