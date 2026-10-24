---
id: TASK-0001
title: 'Phase 0 of bh-02 harness co-evolution: a reproducible measurement loop'
status: To Do
assignee:
  - '@bruk'
created_date: '2026-10-24T20:24:00Z'
labels:
  - size-8
dependencies: []
type: intent
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Problem
I want bh-02 and the model it drives (Ornith) to improve together: the harness changes judged by evals, the weights trained on the trajectories that pass. Training on my Modal subscription has never worked for me, and nothing measures bh-02 today, so neither loop can start. Phase 0 is the floor under both: run bh-02 at a pinned SHA against Ornith-9B on a Modal GPU, judge the result somewhere bh-02 cannot touch, and know how noisy that score is before anything gets promoted on it. It runs from Anthropic's Managed Agents, with Modal for compute.

The plan is the Claude Doc 'bh02 harness co-evolution on Managed Agents' (https://claude.ai/artifact/NUb7R17h512yLGXmzg4P4i). Choices made for this build: a new repository, bh02-lab; Phase 0 only; the lab's operators call the gate through a CLI over HTTPS from Anthropic's cloud sandbox with a narrow token; Ornith served on a Modal GPU.

## Affected users and systems
Me, as the one running the lab. bh-02 (read at a pinned SHA, never changed by this work). Modal: GPU serving, sandboxes, volumes, web endpoints. Anthropic Managed Agents: the lab lead agent and its cloud environment. Hugging Face: Ornith-1.5-9B and the Qwen3.5 proof model.

## Open questions
- [ ] Does bh-02's brig jail (bubblewrap) run inside a Modal Sandbox (gVisor), or does the sandbox have to be the jail? Carried forward: a spike under this intent; the runner works either way, with the sandbox as the outer jail
- [ ] Do harness edits the model makes (extensions in .bh-02/plugins) carry across sessions in a rollout, or does each rollout start from the genome alone? Carried forward: Phase 0 measures the incumbent genome only; this decides Phase 1's bridge
- [ ] Is the Sonnet 5.5 lead with an Opus 4.8 advisor the right pairing, or is the lead alone enough for Phase 0? Carried forward: Phase 0 ships the lead alone; the advisor is one block in its config to add after the first runs
- [ ] How many seeds (k) does the noise band need on the core battery? Carried forward: the noise-measurement task answers it from data
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A rollout runs bh-02 at a pinned image digest headless against Ornith-1.5-9B served on Modal, and emits a workspace snapshot and a transcript, each named by content hash
- [ ] #2 The judge verifies a snapshot in a fresh environment, never imports bh-02, and keeps its verdicts in its own store keyed by snapshot hash
- [ ] #3 The gate's run_battery(genome, model) returns a score with its noise band, and two runs of the incumbent reproduce a hand-run score within that band
- [ ] #4 The lab lead on Managed Agents calls the gate only through the bh02-lab CLI over HTTPS with a token that can do nothing but call the gate
- [ ] #5 An Unsloth SFT proof run (Qwen3.5-0.8B, 60 steps) on Modal produces an adapter that samples correctly with the chat template used at serve time
- [ ] #6 No Claude API key reaches a Modal host, and no Modal or training credential reaches the rollout sandbox or the Managed Agents environment
- [ ] #7 bh-02 itself is not changed: everything the lab needs is a --patch layer, a models file or code in bh02-lab
- [ ] #8 scripts/check is the one definition of green and passes
<!-- AC:END -->
