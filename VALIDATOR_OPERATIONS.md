# Validator Operations Guide

**Subnet**: SN11 (TrajectoryRL)
**Date**: 2026-02-23

> Operational guidance for running a TrajectoryRL validator. For mechanism design and scoring rules, see [INCENTIVE_MECHANISM.md](INCENTIVE_MECHANISM.md).

---

## Cost Asymmetry: Validators Pay, Miners Don't

Validators bear **all LLM inference costs** — they run ClawBench episodes against each miner's policy pack. Miners submit static policy packs (JSON) and pay zero inference cost per epoch; their only costs are registration and R&D iteration.

This is by design: miners compete on *intelligence* (better prompts/policies), not on compute.

## Validator Cost Model

Each eval_interval (4h), a validator evaluates active miners marked for re-evaluation — either their `pack_hash` changed or `eval_interval` has elapsed since last eval:

```
episodes_per_eval      = scenarios(5) × 1 run each = 5 per miner
max_evals_per_day      = 24h / eval_interval(4h) = 6
episodes_per_day       = marked_miners × 5 × evals_triggered
```

> **EMA accumulation**: Validators re-evaluate packs periodically (even if unchanged) to accumulate per-scenario EMA samples and smooth out LLM non-determinism. Rate-limited to at most 1 eval per miner per eval_interval.

**Per-episode token estimate** (averaged across 5 scenarios):

| Component | Tokens |
|-----------|--------|
| System prompt (miner's AGENTS.md) | ~300 |
| User message | ~80 |
| Workspace context (USER.md) | ~220 |
| Fixture data (emails, calendar, tasks) | ~1,600 |
| **Total input** | **~2,200** |
| **Output (agent response)** | **~900** |

## Daily Cost Projections

Designated model: `anthropic/claude-sonnet-4-5-20250929` ($3/M input, $15/M output). Cost per episode ≈ **$0.020**. Will switch to `claude-sonnet-4-6` once OpenClaw supports it (same pricing).

**All validators must use the designated model.** This is a consensus requirement: if validators use different models, agents produce different tool-call sequences, leading to different rubric outcomes and validator disagreement on scores. Using the wrong model puts your validator out of consensus and risks down-weighting by Yuma.

### Worst-case: all miners re-evaluated every eval_interval

| Active Miners | Episodes/day | Daily Cost | Monthly Cost |
|:-------------:|:------------:|:----------:|:------------:|
| 5 | 150 | **$3.00** | **$90** |
| 14 | 420 | **$8.40** | **$252** |
| 30 | 900 | **$18.00** | **$540** |
| 64 | 1,920 | **$38.40** | **$1,152** |
| 128 | 3,840 | **$76.80** | **$2,304** |
| 256 | 7,680 | **$153.60** | **$4,608** |

**Worst-case formula**: `daily_cost ≈ miners × 6 evals × 5 episodes × $0.02 = miners × $0.60/day`.

In practice, miners in steady state rarely change packs more than once per day. A typical day with 30 miners and 2 changed packs evaluates ~10 episodes for new packs plus periodic re-evals of stable packs for EMA accumulation.

## Miner Cost Model

| Cost Item | Estimate |
|-----------|----------|
| Policy iteration (prompt tuning) | Engineer time only |
| Local testing via ClawBench | ~$0.02/episode × ~50 test runs ≈ **$1/iteration** |
| GitHub repo hosting | Free |
| Bittensor registration | ~200 TAO (one-time) |
| **Ongoing operational cost** | **~$0/month** |

## Cost Reduction Levers

1. **Rate limiting** (built-in): At most 1 eval per miner per eval_interval (4h), regardless of how often the miner updates their commitment. Prevents API budget drain from rapid submissions
2. **EMA convergence**: Once a pack's EMA scores stabilize, re-evaluation adds diminishing value. Future optimization: skip re-eval when EMA variance is below threshold
3. **Prompt caching**: Anthropic prompt caching saves ~80% on input tokens (fixture data is identical across runs for the same scenario)

## Sustainability

Validator economics depend on alpha earnings (convertible to TAO) exceeding LLM costs.

Validators earn **subnet alpha**, not TAO directly. Alpha can be swapped for TAO via the subnet's liquidity pool at a market-determined rate. Current SN11 alpha price: ~$2.64 (≈0.015 TAO at ~$180/TAO).

```
Estimated alpha earnings (medium stake ~5k TAO, ~10% validator weight):
  ~295 alpha/day ≈ 4 TAO-equivalent at current pool rate ≈ $720/day

Example (30 miners, worst-case all re-evaluated every interval):
  Daily costs:   30 × $0.60 = $18.00/day
  Daily revenue: ~$720/day (alpha, at current pool rate)
  Net profit:    ~$702/day (~98% margin)

Example (30 miners, typical day):
  Daily costs:   ~$5-10/day (mix of re-evals and stable packs)
  Daily revenue: ~$720/day
  Net profit:    ~$710/day
```

**At current rates**, TrajectoryRL validators are highly profitable:

| Scenario | Daily Cost (worst-case) | Daily Revenue (~$720 alpha) | Monthly Profit |
|----------|:----------:|:---------------------------:|:--------------:|
| 30 miners | $18.00 | $720 | **$21,060** |
| 64 miners | $38.40 | $720 | **$20,448** |
| 128 miners | $76.80 | $720 | **$19,296** |
| 256 miners | $153.60 | $720 | **$16,992** |

Even at 256 miners (worst case, all re-evaluated every eval_interval), LLM costs are only **~21%** of validator alpha revenue.

**Break-even analysis**: At 256 miners ($153.60/day worst-case cost), the alpha-TAO pool rate would need to drop ~5x from current levels before validators become unprofitable. Note: these figures fluctuate with pool exchange rates and subnet demand.

## Weight Setting

Each validator sets weights independently based on its own evaluation data. There is no shared score repo or off-chain consensus mechanism.

Every tempo (~72 min), the validator:
1. Computes `final_score` for each miner from its per-scenario EMA state
2. Selects the winner (highest `final_score`, subject to first-mover delta threshold)
3. Maps miner hotkeys to UIDs via the current metagraph
4. Sets weights on-chain via commit-reveal

Cross-validator consensus is handled entirely on-chain by **YC3 (Yuma Consensus 3)** with **Liquid Alpha**, which dynamically adjusts per-bond learning rates based on how well validators agree.

For full details on EMA scoring, winner selection, and YC3 configuration, see [INCENTIVE_MECHANISM.md — Validator Consensus](INCENTIVE_MECHANISM.md#validator-consensus).

---

## Automatic Updates

Validators should run with `docker compose watch` to automatically pick up new scenarios, scoring updates, and code changes without manual container rebuilds.

### Starting with auto-update

```bash
# Start all services with file watching enabled
docker compose up --watch
```

### How it works

When the team releases updates (new scenarios, scoring fixes, validator code), pull the changes:

```bash
git pull --recurse-submodules
```

`docker compose watch` detects the file changes and automatically applies them:

| Change type | Action | Downtime |
|-------------|--------|----------|
| Validator source code (`trajectoryrl/`, `neurons/`) | Sync + restart | Seconds |
| ClawBench scenarios, fixtures, scoring (`clawbench/`) | Sync + restart | Seconds |
| Mock tools server (`clawbench/clawbench/mock_tools/`) | Sync + restart | Seconds |
| Dependencies (`requirements.txt`, `pyproject.toml`) | Full rebuild | Minutes |
| Dockerfile changes | Full rebuild | Minutes |

### Optional: automated git pull

Set up a cron job to pull updates periodically:

```bash
# Pull every 6 hours (add to crontab -e)
0 */6 * * * cd /path/to/trajectoryrl && git pull --recurse-submodules >> /var/log/trajectoryrl-pull.log 2>&1
```

### Running without auto-update

If you prefer manual control, run detached without watch:

```bash
docker compose up -d

# After pulling updates, manually rebuild:
git pull --recurse-submodules
docker compose up -d --build
```
