# TrajectoryRL Public API

Base URL: `https://trajrl.com`

All endpoints are read-only `GET` requests. No authentication required. All responses are JSON.

---

## Table of Contents

- [Scores by Validator](#get-apiscoresby-validator)
- [Validators](#get-apivalidators)
- [Miner Detail](#get-apiminershotkey)
- [Pack Detail](#get-apiminershotkeypacks-packhash)
- [Recent Submissions](#get-apisubmissions)
- [Eval Logs](#get-apieval-logs)

---

## GET /api/scores/by-validator

Latest evaluation results from a specific validator for all miners it reported on in the last 24 hours.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `validator` | string | Yes | Validator SS58 hotkey |

### Response

| Field | Type | Description |
|-------|------|-------------|
| `validator` | string | The queried validator hotkey |
| `entries` | array | Per-miner evaluation entries (see below) |

#### `entries[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `minerHotkey` | string | Miner SS58 hotkey |
| `uid` | number \| null | Miner on-chain UID |
| `qualified` | boolean | Whether this miner qualified |
| `costUsd` | number \| null | Evaluation cost in USD |
| `score` | number \| null | Evaluation score |
| `weight` | number \| null | On-chain weight assigned to this miner |
| `scenarioScores` | object \| null | Per-scenario evaluation results (keyed by scenario name) |
| `packHash` | string \| null | Pack hash |
| `blockHeight` | number | Block height of this evaluation |
| `createdAt` | string | ISO 8601 timestamp of this report |
| `rejected` | boolean | Whether this eval was a pre-eval rejection |
| `rejectionStage` | string \| null | Rejection stage (`"pack_fetch"` \| `"schema_validation"` \| `"integrity_check"`) |
| `rejectionDetail` | string \| null | Human-readable rejection reason |
| `llmModel` | string \| null | LLM model used by this validator |

### Response Example

```json
{
  "validator": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
  "entries": [
    {
      "minerHotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
      "uid": 7,
      "qualified": true,
      "costUsd": 8.42,
      "score": 0.95,
      "weight": 1.0,
      "scenarioScores": {
        "client_escalation": {
          "score": 1.0,
          "cost": 4.2,
          "qualified": true
        }
      },
      "packHash": "abc123def456...",
      "blockHeight": 4215678,
      "createdAt": "2026-03-23T10:30:00.000Z",
      "rejected": false,
      "rejectionStage": null,
      "rejectionDetail": null,
      "llmModel": "claude-sonnet-4-6"
    }
  ]
}
```

### Errors

| Status | Body |
|--------|------|
| 400 | `{ "error": "validator query parameter is required" }` |
| 500 | `{ "error": "Internal server error" }` |

---

## GET /api/validators

List of all validators with heartbeat status, software version, LLM model, and operational timestamps.

### Response

| Field | Type | Description |
|-------|------|-------------|
| `validators` | array | Validator entries |

#### `validators[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `hotkey` | string | Validator SS58 hotkey |
| `version` | string \| null | Running software version (e.g. `"1.2.0"`) |
| `lastSeen` | string \| null | ISO 8601 timestamp of the most recent heartbeat |
| `lastSetWeightsAt` | string \| null | ISO 8601 timestamp of the last `set_weights` call |
| `lastEvalAt` | string \| null | ISO 8601 timestamp of the last evaluation completion |
| `llmModel` | string \| null | LLM model used by this validator (e.g. `"claude-sonnet-4-6"`) |
| `latestReport` | string \| null | ISO 8601 timestamp of the most recent score report |

### Response Example

```json
{
  "validators": [
    {
      "hotkey": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
      "version": "1.2.0",
      "lastSeen": "2026-03-23T10:30:00.000Z",
      "lastSetWeightsAt": "2026-03-23T10:28:00.000Z",
      "lastEvalAt": "2026-03-23T10:25:00.000Z",
      "llmModel": "claude-sonnet-4-6",
      "latestReport": "2026-03-23T10:28:00.000Z"
    },
    {
      "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
      "version": "1.1.8",
      "lastSeen": "2026-03-23T10:26:00.000Z",
      "lastSetWeightsAt": "2026-03-23T10:20:00.000Z",
      "lastEvalAt": "2026-03-23T10:22:00.000Z",
      "llmModel": "claude-sonnet-4-6",
      "latestReport": "2026-03-23T10:25:00.000Z"
    }
  ]
}
```

---

## GET /api/miners/:hotkey

Detailed evaluation data for a specific miner, including per-validator breakdowns, scenario summaries, recent submissions, and ban status.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `hotkey` | string | Miner SS58 hotkey |

### Response

| Field | Type | Description |
|-------|------|-------------|
| `hotkey` | string | Miner SS58 hotkey |
| `ownerkey` | string \| null | Miner's coldkey (owner key) |
| `uid` | number \| null | On-chain UID |
| `rank` | number \| null | Leaderboard rank |
| `isBanned` | boolean | Whether the miner's owner is currently banned |
| `bannedUntil` | string \| null | ISO 8601 ban expiry timestamp |
| `banRecord` | object \| null | Ban details (see below) |
| `qualified` | boolean | Aggregated qualification status |
| `totalCostUsd` | number \| null | Aggregated cost in USD |
| `score` | number \| null | Aggregated score |
| `validatorCount` | number | Number of validators that reported |
| `confidence` | string | Consensus confidence (`"high"`, `"medium"`, `"low"`) |
| `coverage` | number \| null | Validator coverage fraction |
| `costDeviation` | number \| null | Cost standard deviation across validators |
| `scoreDeviation` | number \| null | Score standard deviation across validators |
| `hasDivergence` | boolean | Whether validator reports diverge significantly |
| `isActive` | boolean | Whether the miner is active |
| `isBootstrap` | boolean | Whether the network is in bootstrap mode |
| `packHash` | string \| null | Current pack hash |
| `gcsPackUrl` | string \| null | Pack URL (GCS-hosted) |
| `commitBlock` | number \| null | Block when pack was committed |
| `scenarioSummary` | array | Per-scenario aggregated stats (see below) |
| `validators` | array | Per-validator evaluation breakdown (see below) |
| `recentSubmissions` | array | Last 5 pack submissions (see below) |

#### `banRecord` fields (when present)

| Field | Type | Description |
|-------|------|-------------|
| `failedPackCount` | number | Number of packs that failed integrity checks |
| `failedPacks` | array | List of failed pack entries |
| `bannedAt` | string \| null | ISO 8601 ban start timestamp |
| `bannedUntil` | string \| null | ISO 8601 ban expiry timestamp |
| `isBanned` | boolean | Whether the ban is active |

#### `scenarioSummary[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Scenario name |
| `avgCost` | number \| null | Average cost across validators |
| `avgScore` | number \| null | Average score across validators |
| `qualCount` | number | Number of validators that qualified this scenario |
| `validatorCount` | number | Number of validators that reported this scenario |

#### `validators[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `hotkey` | string | Validator SS58 hotkey |
| `name` | string \| null | Validator display name |
| `qualified` | boolean | Whether this validator qualified the miner |
| `costUsd` | number \| null | Cost reported by this validator |
| `score` | number \| null | Score reported by this validator |
| `blockHeight` | number \| null | Block height of the report |
| `createdAt` | string | ISO 8601 timestamp of the report |
| `rejected` | boolean | Whether this was a pre-eval rejection |
| `rejectionStage` | string \| null | Rejection stage |
| `rejectionDetail` | string \| null | Rejection reason |
| `scenarios` | array | Per-scenario breakdown: `{ name, cost, score, qualified }` |

#### `recentSubmissions[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `packHash` | string | Pack hash |
| `gcsPackUrl` | string \| null | Pack URL (GCS-hosted) |
| `qualified` | boolean \| null | Qualification status |
| `totalCostUsd` | number \| null | Total cost |
| `score` | number \| null | Score |
| `evalStatus` | string | Pre-eval result (`"passed"` or `"failed"`) |
| `evalReason` | string \| null | Pre-eval failed reason |
| `submittedAt` | string | ISO 8601 submission timestamp |
| `evaluatedAt` | string \| null | ISO 8601 evaluation timestamp |

### Response Example

```json
{
  "hotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
  "ownerkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
  "uid": 7,
  "rank": 1,
  "isBanned": false,
  "bannedUntil": null,
  "banRecord": null,
  "qualified": true,
  "totalCostUsd": 8.42,
  "score": 0.95,
  "validatorCount": 8,
  "confidence": "high",
  "coverage": 1.0,
  "costDeviation": 0.15,
  "scoreDeviation": 0.02,
  "hasDivergence": false,
  "isActive": true,
  "isBootstrap": false,
  "packHash": "abc123def456...",
  "gcsPackUrl": "https://storage.googleapis.com/...",
  "commitBlock": 4215000,
  "scenarioSummary": [
    {
      "name": "client_escalation",
      "avgCost": 4.2,
      "avgScore": 0.95,
      "qualCount": 8,
      "validatorCount": 8
    }
  ],
  "validators": [
    {
      "hotkey": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
      "name": "validator-1",
      "qualified": true,
      "costUsd": 8.5,
      "score": 0.96,
      "blockHeight": 4215678,
      "createdAt": "2026-03-23T10:30:00.000Z",
      "rejected": false,
      "rejectionStage": null,
      "rejectionDetail": null,
      "scenarios": [
        { "name": "client_escalation", "cost": 4.2, "score": 1.0, "qualified": true }
      ]
    }
  ],
  "recentSubmissions": [
    {
      "packHash": "abc123def456...",
      "gcsPackUrl": "https://storage.googleapis.com/...",
      "qualified": true,
      "totalCostUsd": 8.42,
      "score": 0.95,
      "evalStatus": "passed",
      "evalReason": null,
      "submittedAt": "2026-03-23T09:00:00.000Z",
      "evaluatedAt": "2026-03-23T09:05:00.000Z"
    }
  ]
}
```

### Errors

| Status | Body |
|--------|------|
| 500 | `{ "error": "Internal server error" }` |

---

## GET /api/miners/:hotkey/packs/:packHash

Detailed evaluation data for a specific miner's pack, including per-validator and per-scenario breakdowns.

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `hotkey` | string | Miner SS58 hotkey |
| `packHash` | string | Pack SHA-256 hash |

### Response

| Field | Type | Description |
|-------|------|-------------|
| `packHash` | string | Pack hash |
| `gcsPackUrl` | string \| null | Pack URL (GCS-hosted) |
| `evalStatus` | string | Pre-eval result (`"passed"`, `"failed"`, or `"pending"`) |
| `evalReason` | string \| null | Pre-eval failed reason |
| `submittedAt` | string \| null | ISO 8601 submission timestamp |
| `evaluatedAt` | string \| null | ISO 8601 evaluation timestamp |
| `minerHotkey` | string | Miner hotkey |
| `minerUid` | number \| null | Miner UID |
| `minerColdkey` | string \| null | Miner coldkey (owner key) |
| `summary` | object | Aggregated scoring summary (see below) |
| `scenarios` | string[] | Scenario names present in validator reports |
| `validators` | array | Per-validator breakdown (see below) |

#### `summary` fields

| Field | Type | Description |
|-------|------|-------------|
| `qualified` | boolean | `true` only if all validators qualified this pack |
| `qualifiedCount` | number | Number of validators that qualified |
| `bestCost` | number \| null | Lowest cost among qualified reports |
| `avgCost` | number \| null | Average cost across all reports |
| `validatorCount` | number | Total validators that reported on this pack |

#### `validators[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `hotkey` | string | Validator hotkey |
| `name` | string \| null | Validator display name |
| `qualified` | boolean | Whether qualified by this validator |
| `costUsd` | number \| null | Cost reported |
| `score` | number \| null | Score reported |
| `blockHeight` | number | Block height |
| `createdAt` | string | ISO 8601 report timestamp |
| `rejected` | boolean | Whether pre-eval rejected |
| `rejectionStage` | string \| null | Rejection stage |
| `rejectionDetail` | string \| null | Rejection reason |
| `scenarios` | array | Per-scenario results: `{ name, cost, score, qualified }` |

### Response Example

```json
{
  "packHash": "abc123def456...",
  "gcsPackUrl": "https://storage.googleapis.com/...",
  "evalStatus": "passed",
  "evalReason": null,
  "submittedAt": "2026-03-23T09:00:00.000Z",
  "evaluatedAt": "2026-03-23T09:05:00.000Z",
  "minerHotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
  "minerUid": 7,
  "minerColdkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
  "summary": {
    "qualified": true,
    "qualifiedCount": 8,
    "bestCost": 7.9,
    "avgCost": 8.42,
    "validatorCount": 8
  },
  "scenarios": ["client_escalation", "morning_brief"],
  "validators": [
    {
      "hotkey": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
      "name": "validator-1",
      "qualified": true,
      "costUsd": 8.5,
      "score": 0.96,
      "blockHeight": 4215678,
      "createdAt": "2026-03-23T10:30:00.000Z",
      "rejected": false,
      "rejectionStage": null,
      "rejectionDetail": null,
      "scenarios": [
        { "name": "client_escalation", "cost": 4.2, "score": 1.0, "qualified": true },
        { "name": "morning_brief", "cost": 4.3, "score": 0.92, "qualified": true }
      ]
    }
  ]
}
```

### Errors

| Status | Body |
|--------|------|
| 500 | `{ "error": "Internal server error" }` |

---

## GET /api/submissions

Most recent 100 pack submissions that have completed evaluation (passed or failed).

### Response

| Field | Type | Description |
|-------|------|-------------|
| `submissions` | array | Submission entries (see below) |

#### `submissions[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | number | Submission record ID |
| `minerHotkey` | string | Miner SS58 hotkey |
| `packHash` | string | Pack hash |
| `evalStatus` | string | Pre-eval result (`"passed"` or `"failed"`) |
| `evalReason` | string \| null | Pre-eval failed reason |
| `submittedAt` | string | ISO 8601 submission timestamp |
| `evaluatedAt` | string \| null | ISO 8601 evaluation completion timestamp |

### Response Example

```json
{
  "submissions": [
    {
      "id": 1234,
      "minerHotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
      "packHash": "abc123def456...",
      "evalStatus": "passed",
      "evalReason": null,
      "submittedAt": "2026-03-23T09:00:00.000Z",
      "evaluatedAt": "2026-03-23T09:05:00.000Z"
    },
    {
      "id": 1233,
      "minerHotkey": "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
      "packHash": "def789ghi012...",
      "evalStatus": "failed",
      "evalReason": "hard-coded responses detected",
      "submittedAt": "2026-03-23T08:50:00.000Z",
      "evaluatedAt": "2026-03-23T08:55:00.000Z"
    }
  ]
}
```

---

## GET /api/eval-logs

Evaluation log archives uploaded by validators. Each entry contains a GCS URL to a `.tar.gz` archive with detailed eval logs.

There are two log types:
- **`miner`** — Per-miner eval log containing scenario-level tool calls, HTTP requests, judge scores, and cost breakdowns for a single miner evaluation.
- **`cycle`** — Eval cycle summary log covering the entire eval cycle: metagraph sync, miner enumeration, pre-eval rejections, per-miner eval timing, and the cycle completion summary.

Use `type=cycle` to retrieve only the cycle summary logs. Use `type=miner` combined with `miner` or `pack_hash` to drill into a specific miner's eval details.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `validator` | string | No | Filter by validator SS58 hotkey |
| `miner` | string | No | Filter by miner SS58 hotkey |
| `type` | string | No | `"miner"` for per-miner eval logs, `"cycle"` for eval cycle summary logs |
| `eval_id` | string | No | Filter by eval cycle identifier (e.g. `"20260323_143025"`) |
| `pack_hash` | string | No | Filter by pack hash |
| `from` | string | No | Start of date range filter (ISO 8601, e.g. `"2026-03-23T00:00:00Z"`) |
| `to` | string | No | End of date range filter (ISO 8601, e.g. `"2026-03-23T23:59:59Z"`) |
| `limit` | number | No | Max number of results to return (default: 50, max: 200) |
| `offset` | number | No | Number of results to skip for pagination (default: 0) |

### Query Examples

Get all cycle summary logs from a specific validator:

```
GET /api/eval-logs?validator=5FFApaS7...&type=cycle
```

Get all log archives for a specific eval cycle (summary + all per-miner logs):

```
GET /api/eval-logs?eval_id=20260323_143025
```

Get per-miner eval logs for a specific miner on a specific date:

```
GET /api/eval-logs?miner=5GrwvaEF...&type=miner&from=2026-03-23T00:00:00Z&to=2026-03-23T23:59:59Z
```

### Response

| Field | Type | Description |
|-------|------|-------------|
| `logs` | array | Eval log entries (see below) |

#### `logs[]` fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Log record ID |
| `validatorHotkey` | string | Validator that uploaded this log |
| `evalId` | string | Eval cycle identifier (`YYYYMMDD_HHMMSS` format) |
| `logType` | string | `"miner"` (per-miner eval log) or `"cycle"` (full cycle log) |
| `minerHotkey` | string \| null | Miner hotkey (null for cycle-level logs) |
| `minerUid` | number \| null | Miner UID (null for cycle-level logs) |
| `packHash` | string \| null | Pack hash evaluated (null for cycle-level logs) |
| `blockHeight` | number | Block height at eval time |
| `gcsUrl` | string | Public URL to the `.tar.gz` log archive on GCS |
| `sizeBytes` | number | Archive size in bytes |
| `createdAt` | string | ISO 8601 upload timestamp |

### Response Example

```json
{
  "logs": [
    {
      "id": "42",
      "validatorHotkey": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
      "evalId": "20260323_143025",
      "logType": "miner",
      "minerHotkey": "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
      "minerUid": 7,
      "packHash": "abc123def456...",
      "blockHeight": 4215678,
      "gcsUrl": "https://storage.googleapis.com/bucket/logs/abc123/20260323_143025/miner.tar.gz",
      "sizeBytes": 245760,
      "createdAt": "2026-03-23T14:30:30.000Z"
    },
    {
      "id": "41",
      "validatorHotkey": "5FFApaS75bvpgP9gQ5hTUdZHiTc6LB2VPP9gvHN6VQCNug6f",
      "evalId": "20260323_143025",
      "logType": "cycle",
      "minerHotkey": null,
      "minerUid": null,
      "packHash": null,
      "blockHeight": 4215678,
      "gcsUrl": "https://storage.googleapis.com/bucket/logs/abc123/__cycle__/4215678.tar.gz",
      "sizeBytes": 51200,
      "createdAt": "2026-03-23T14:35:00.000Z"
    }
  ]
}
```

### Errors

| Status | Body |
|--------|------|
| 400 | `{ "error": "type must be \"miner\" or \"cycle\"" }` |
| 500 | `{ "error": "Internal server error" }` |

---

## Rate Limiting

- There is no server-side rate limiting enforced, but consumers should poll at reasonable intervals (recommended: ≥ 30 seconds).

## Error Handling

All endpoints return JSON error responses. Common patterns:

| Status | Description |
|--------|-------------|
| 200 | Success (some endpoints return fallback data with a `message` field on empty state) |
| 400 | Missing or invalid query parameters |
| 500 | Internal server error |
| 502 | Upstream data source unavailable (fallback empty data returned) |
