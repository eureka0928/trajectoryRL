# trajrl

CLI for the [TrajectoryRL subnet](https://trajrl.com) (Bittensor SN11). Query live validator, miner, and evaluation data from the terminal.

Designed for AI agents (Claude Code, Cursor) and humans alike — outputs JSON when piped, Rich tables when interactive.

## Install

```bash
pip install trajrl
```

## Commands

```
trajrl status                       # Network health overview
trajrl validators                   # List all validators
trajrl scores <validator_hotkey>    # Per-miner scores from a validator
trajrl miner <hotkey>               # Miner detail + diagnostics
trajrl pack <hotkey> <pack_hash>    # Pack evaluation detail
trajrl submissions [--failed]       # Recent pack submissions
trajrl eval-history <validator>     # List eval cycle IDs for a validator
trajrl cycle-log <validator>        # Download and display a cycle log
trajrl logs [--type cycle|miner]    # Eval log archives
```

### Global Options

Every command accepts:

| Option | Description |
|--------|-------------|
| `--json` / `-j` | Force JSON output (auto-enabled when stdout is piped) |
| `--base-url URL` | Override API base (default: `https://trajrl.com`, env: `TRAJRL_BASE_URL`) |

## Usage Examples

### Quick network check

```bash
trajrl status
```
```
╭──────────────────── Network Status ────────────────────╮
│   Validators: 7 total, 7 active (seen <1h)             │
│   LLM Models: zhipu/glm-5 (3), chutes/GLM-5-TEE (3)    │
│   Latest Eval: 7h ago                                  │
│   Submissions: 65 passed, 35 failed (last batch)       │
╰────────────────────────────────────────────────────────╯
```

### List validators

```bash
trajrl validators
```
```
 Hotkey         UID  Version  LLM Model              Last Eval   Last Seen
 5Cd6h…sn11     29  0.2.7    chutes/zai-org/GLM-5…   7h ago      2m ago
 5EcgNd…797f   221  0.2.7    zhipu/glm-5             10h ago     6m ago
 ...
```

### Inspect a miner

```bash
trajrl miner 5HMgR6LnNqUAtaKRwa6bLF4Vy4KBf7TaxCLehyff9mWPhSHt
```

Shows rank, qualification status, cost, scenario breakdown, per-validator reports, recent submissions, and ban records.

### View failed submissions

```bash
trajrl submissions --failed
```

### Investigate a validator's eval cycle

First, list recent eval cycles for a validator:

```bash
trajrl eval-history 5Cd6h...
```
```
            Eval IDs (5) — 5Cd6h…sn11
 Eval ID            Validator    Block  Logs  Created
 20260325_060012    5Cd6h…sn11  421890    12  3h ago
 20260324_060008    5Cd6h…sn11  421530    11  1d ago
 20260323_060015    5Cd6h…sn11  421170    13  2d ago
 ...
```

Then fetch the full cycle log for a specific eval:

```bash
trajrl cycle-log 5Cd6h... --eval-id 20260325_060012
```

Or just grab the latest one:

```bash
trajrl cycle-log 5Cd6h...
```

The cycle log contains the complete eval cycle output: metagraph sync, miner enumeration, per-miner eval timing, WEIGHT RESULTS, and set_weights status.

> **Note:** Eval IDs are defined by each validator independently (typically a timestamp like `20260325_060012`). They are **not** globally unique — the same eval ID from different validators refers to different evaluation cycles. Always pair an eval ID with a specific validator hotkey.

### Filter eval logs

```bash
trajrl logs --type cycle --limit 5
trajrl logs --validator 5Cd6h... --type miner
trajrl logs --eval-id 20260324_000340
```

### JSON output for agents

Pipe to any tool — JSON is automatic:

```bash
trajrl validators | jq '.validators[].hotkey'
trajrl scores 5Cd6h... --json | python3 -c "
  import sys, json
  d = json.load(sys.stdin)
  for e in d['entries'][:5]:
      print(f\"{e['minerHotkey'][:12]}  qual={e['qualified']}  cost={e['costUsd']}\")
"
```

Force JSON in an interactive terminal:

```bash
trajrl miner 5HMgR6... --json
```

## API Reference

All data comes from the [TrajectoryRL Public API](https://trajrl.com) — read-only, no authentication required.

| Endpoint | CLI Command |
|----------|-------------|
| `GET /api/validators` | `trajrl validators` |
| `GET /api/scores/by-validator?validator=` | `trajrl scores <hotkey>` |
| `GET /api/miners/:hotkey` | `trajrl miner <hotkey>` |
| `GET /api/miners/:hotkey/packs/:hash` | `trajrl pack <hotkey> <hash>` |
| `GET /api/submissions` | `trajrl submissions` |
| `GET /api/eval-logs` | `trajrl logs` |
| `GET /api/eval-logs?log_type=cycle` | `trajrl eval-history <hotkey>` |
| `GET /api/eval-logs` + GCS download | `trajrl cycle-log <hotkey>` |

## Development

```bash
git clone <repo> && cd trajrl
pip install -e .
trajrl --help
```
