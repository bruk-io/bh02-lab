# bh02-lab

Where [bh-02](https://github.com/bruk-io/bh-02) and the model it drives (Ornith-1.5-9B) are measured, and later improved together. This is Phase 0 of the plan in [bh02 harness co-evolution on Managed Agents](https://claude.ai/artifact/NUb7R17h512yLGXmzg4P4i): a measurement loop that is reproducible, with a judge bh-02 can't touch, and a known noise band.

## How a battery run works

```
lab lead (Managed Agents, cloud sandbox)
  └─ bh02-lab battery run          gate token only; substituted at egress, never seen
       └─ gate (Modal, HTTPS)      owns the job; handles a genome as plugin hashes only
            ├─ rollout.run × (tasks × k)
            │    └─ Modal Sandbox  bh-02 at the pinned SHA, headless; inference key only;
            │                      may reach the serving host and nothing else
            │         └─ bh-02 ─► serve (vLLM, Ornith, Modal GPU)
            │    stores the workspace, transcript and events by content hash
            └─ judge.verify × each rollout
                 └─ Modal Sandbox  Python + pytest + the hidden checks; no network, no secret
                 one verdict per snapshot, task and judge pin, in the judge's own store
```

- **Pinned, not claimed.** A rollout records the rollout image's Modal id, the genome's plugin hashes, the serving hash of every pin that decides the model's answers, and the judge's own pin. `pins.toml` holds the pins.
- **bh-02 is not changed.** `headless/` is a cordis row that takes the place of bh-02's terminal app through `--patch`: it sends the task, records what bh-02 shows and asks, and ends the session.
- **Transcripts are training data, never evidence.** Only the judge's verdicts on the snapshot count.

## Layout

| Path | What |
|---|---|
| `src/bh02_lab/` | the pure core (`contract`, `hashing`, `snapshot`, `stats`, `pins`, `battery`, `gate_core`, `proof`), the gate API, the CLI |
| `src/bh02_lab/modal_apps/` | the Modal apps: `serve`, `rollout`, `judge`, `gate`, `train_proof` |
| `src/bh02_lab/sandbox/entry.py` | the program the rollout sandbox runs; standard library only |
| `headless/` | the headless ui row, installed into bh-02's own environment |
| `battery/core/` | six tasks: `prompt.md`, `repo/`, hidden `check/`, `reference/` |
| `agents/` | the lab lead on Managed Agents; `scripts/apply-agents` applies it |
| `backlog/` | the board: Phase 0's intent and its tasks |

## Checks

```sh
scripts/bh02-checkout ../bh-02-pinned          # once: bh-02 at the pinned SHA, with the headless row
BH02_CHECKOUT=../bh-02-pinned scripts/check   # format, lint, types, tests, headless tests, architecture gate
```

## First run

What needs your credentials, in order. Nothing below has run against Modal or Managed Agents yet.

1. **Secrets.** `modal secret create bh02-lab-inference ORNITH_KEY=$(openssl rand -hex 32)` and `modal secret create bh02-lab-gate GATE_TOKEN=$(openssl rand -hex 32)`. Keep the gate token for step 5.
2. **Training proof** (TASK-0001.09): `modal run -m bh02_lab.modal_apps.train_proof`. Exit 0 means fine-tuning works on Modal.
3. **Serving**: `modal deploy -m bh02_lab.modal_apps.serve`, then check `curl -H "Authorization: Bearer $ORNITH_KEY" <url>/v1/models` lists `Ornith-1.5-9B` and the same request without the key is refused.
4. **Jail spike** (TASK-0001.04): `modal deploy -m bh02_lab.modal_apps.rollout`, then `modal run -m bh02_lab.modal_apps.rollout::probe_jail`. If the jail line says `jailed`, keep `rollout.jail = "bh-02"`; if bh-02 failed to start its jail, set it to `"sandbox"` (or try the `vm` runtime) and say which in the task.
5. **Judge and gate**: `modal deploy -m bh02_lab.modal_apps.judge` and `modal deploy -m bh02_lab.modal_apps.gate`. Then `export BH02_LAB_GATE_URL=<gate url> BH02_LAB_TOKEN=<gate token>` and `uv run bh02-lab pins`.
6. **One battery by hand**: `uv run bh02-lab battery run --k 1`. This is the hand-run score the lead's runs are compared with.
7. **The lead**: `ANTHROPIC_API_KEY=... scripts/apply-agents apply`, then `scripts/apply-agents run --k 5`. The environment installs this repository from GitHub, so push first.
