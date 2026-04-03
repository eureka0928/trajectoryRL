# ClawBench v2: Sandbox Architecture

> Internal design document — next-season redesign of ClawBench and mock tooling.

## Status Quo: What's Wrong with v1

### 1. Stateless Mock Tools — The Fundamental Lie

The biggest architectural problem. Tools don't reflect mutations:

- Agent sends email → `himalaya envelope list` still returns the original inbox
- Agent creates a Notion task → next `databases/query` returns the same tasks
- Agent deletes calendar event → event is still there on next read

We can only score the agent's *intent* (did it try to send?), not its *competence* at handling state transitions. A real agent that sends an email and then confirms delivery would look like a hallucinator because the mock never changes state. **Multi-step workflows where step 2 depends on step 1 are fundamentally untestable.**

### 2. `exec` Is a God-Function with Brittle Regex

The exec handler is a ~170-line chain of regex patterns covering 4 completely different systems (email, tasks, calendar, GitHub):

- **Command variation kills agents**: `himalaya envelope list` works, `himalaya list envelope` doesn't. Flags in unexpected positions break the match.
- **Pattern order is the API contract**: First match wins. Undocumented, untested priority ordering.
- **No argument parsing**: Real CLIs have complex flag/option semantics; the mock just regex-matches the whole string.
- **Creative agents are punished**: An agent that uses `curl` to hit the email API directly instead of `himalaya` gets a generic fallback, even though the approach is equally valid.

This creates a **narrow corridor of "correct" commands** that rewards pattern memorization over genuine capability. Miners learn which exact command strings the mock accepts, not how to be good agents.

### 3. Fixture-Scenario Tight Coupling

Each scenario is an isolated island with manually crafted fixture files:

- **No composability**: Can't mix fixtures across scenarios to create new ones.
- **Manual data maintenance**: Adding a scenario means hand-writing 5-10 JSON fixture files with realistic, cross-referenced data. Slow and error-prone.
- **No parameterized variation**: Can't test "same scenario but 50 emails instead of 5." Only variation is the user persona (name/role/company) — superficial.
- **No schema enforcement**: A typo in a fixture (`"subjct"` instead of `"subject"`) silently breaks a scenario.

### 4. Single-Turn, Single-Episode Limitation

Every episode: one user message → agent tool-calls → one final response. Can't express:

- **Approval flows**: "Draft this email, wait for my approval, then send"
- **Clarification dialogues**: Agent asks follow-up, user responds
- **Long-running tasks**: Agent hits a blocker, reports back, user provides more info
- **Follow-up evaluation**: "Good brief, now add the budget numbers"

These are core knowledge-worker patterns the benchmark can't test. Miners have zero incentive to handle multi-turn interactions.

### 5. No Error Simulation

Tools always succeed (or return generic 404 for missing fixtures). No rate limits, auth failures, timeouts, partial responses, service degradation. We can't evaluate agent **robustness** — only happy-path behavior.

### 6. Knowledge-Worker Monoculture

All 7 scenarios are office-worker email/calendar/tasks/Slack. No code tasks, data analysis, customer support, research, creative work. Miners over-specialize for one narrow domain. The "cost competition" is really "who can write the shortest AGENTS.md that passes these 7 office scenarios."

### 7. Validator-ClawBench Coupling Is Fragile

- Subprocess-based with implicit JSON contract (parses **last line** as JSON)
- Any stray print/log to stdout breaks parsing
- Judge has hardcoded formatting for each tool type — adding a new tool requires judge code changes
- Workspace path, env var names, flag contracts are all implicit, no schema

### 8. Fixed Fixture Pool Enables Memorization

Miners can read every fixture email/task/event (open-source ClawBench). They see exactly which criteria exist. With only 7 scenarios and static data, miners can and do memorize the benchmark. The LLM integrity judge catches blatant gaming, but subtle optimization-through-memorization is structurally indistinguishable from genuine capability.

### 9. No Cost Measurement for Tool Calls

Cost only tracks LLM token usage. Real deployments have API rate limits, network latency, and service charges. A pack making 20 tool calls at $0.02 LLM cost isn't necessarily cheaper than 3 tool calls at $0.03 when you factor in real API overhead.

### 10. Toy Memory Implementation

`memory_search` does line-by-line keyword matching on markdown files. Agents that build sophisticated retrieval strategies get no benefit — the mock always returns the same keyword-matched lines.

---

## The Core Idea: Docker Sandbox Evaluation

Instead of mock tool handlers that regex-match commands and return static fixtures, **the agent SSHs/execs into a prepared Docker sandbox** where real (mock) services run with real protocols and stateful behavior.

### Current Architecture

```
Agent → OpenClaw API → mock handler → regex match → static fixture → canned response
```

### v2 Architecture

```
Agent → SSH/exec into Docker → real shell → real (mock) services → stateful environment
```

---

## Why This Changes Everything

### The Exec God-Function Dies

No more regex matching. The agent runs **real commands in a real shell**. Want to check email?

- `himalaya envelope list` (CLI)
- `curl localhost:1080/api/v2/messages` (HTTP API)
- `python3 -c "import imaplib; ..."` (programmatic)
- `cat /var/mail/user/new/*` (raw maildir)
- Chain commands with pipes, write scripts, use `jq`

All valid. All produce real results. The benchmark tests **can the agent accomplish the task**, not **does the agent know our exact mock API**.

### Statefulness Comes for Free

Agent sends email → it actually appears in the mock SMTP server's mailbox. Agent creates a task → the mock API's database has a new row. Agent deletes a calendar event → it's gone.

Scoring becomes: **inspect the final state of the environment**. Not "did the agent call the right regex pattern," but "is there actually an email in the sent folder addressed to Dana with the right subject?"

### Memorization Becomes Nearly Impossible

With real services and procedural fixture generation:

- Seed different data each eval (deterministic from `epoch_seed`)
- Same scenario structure, completely different emails/tasks/people
- Agent can't hardcode "read msg_003" because msg_003 doesn't exist this time
- There might be 15 messages with different IDs, different senders, different urgency

### The "Narrow Corridor" Opens Wide

Current: only ONE valid way to read email (exact `himalaya` command string).

Sandbox: agent picks its own approach. The mock services respond to **real protocols**, not pattern-matched strings. Creative, efficient agents are rewarded instead of punished.

---

## Sandbox Container Architecture

```
Docker Container ("eval sandbox")
├── Tier 1: Deterministic Mock Services (stateful, real protocols)
│   ├── MailHog/MailPit       (SMTP :1025, HTTP API :1080) — email
│   ├── Mock Notion API       (HTTP :8080) — tasks / databases
│   ├── Mock Calendar API     (CalDAV :5232 or HTTP :8081)
│   ├── Mock Slack API        (HTTP :8082) — channels, messages
│   └── Mock GitHub / Gitea   (HTTP :3000) — repos, PRs, issues
│
├── Tier 2: LLM-Backed Runtime Mocks (read-only, on-the-fly generation)
│   ├── Web search/fetch proxy (HTTP :8083) — LLM generates search results & pages
│   └── Memory service         (HTTP :8084) — LLM generates memory entries
│   (Requires outbound access to LLM API only — all other egress blocked)
│
├── CLI Tools (pre-installed)
│   ├── himalaya, gh, curl, jq, python3, git, etc.
│   └── ~/.config/ pre-configured to point at local mock services
│
├── Workspace
│   ├── /workspace/AGENTS.md   (miner's pack)
│   ├── /workspace/...         (pack files)
│   └── /workspace/docs/       (scenario-specific reference docs)
│
├── Seed Data (LLM-generated from scenario template + epoch_seed, Tier 3)
│   ├── Pre-loaded emails in MailHog
│   ├── Pre-loaded tasks in mock Notion
│   ├── Pre-loaded calendar events
│   └── Pre-loaded Slack channel history
│
└── Security
    ├── Network: egress blocked EXCEPT validator's LLM API (for Tier 2 mocks)
    ├── CPU / memory / disk limits
    └── Hard timeout per episode
```

---

## LLM-Hybrid Mock Strategy

The original sandbox design assumes building dedicated mock services for every tool (MailHog, mock Notion, mock Slack, etc.). This is robust but expensive to build and maintain. A hybrid approach uses **LLM generation at two levels** — as a fixture factory at build time, and as a runtime mock for select tools — while keeping deterministic services where state inspection matters.

### The Three-Tier Architecture

```
Tier 1: Deterministic Mock Services (stateful, scoring inspects state)
        → Email (MailHog/MailPit), Tasks, Calendar, Slack, GitHub/Gitea
        → Agent mutations are real, state is inspectable after episode
        → Seed data: LLM-generated (Tier 3), loaded before episode

Tier 2: LLM-Backed Runtime Mocks (read-only, scoring checks outcomes)
        → web_search, web_fetch, memory_search
        → LLM generates realistic responses on the fly during episode
        → No state to inspect — scoring evaluates agent's final output

Tier 3: LLM Fixture Factory (build-time generation)
        → Generates all seed data for Tier 1 services
        → Replaces hand-crafted JSON fixture files
        → Deterministic distribution via hash consensus
```

### Why Three Tiers?

The key insight is that tools fall into two categories based on how scoring works:

| Service | Mutations? | Scoring Method | → Tier |
|---------|-----------|----------------|--------|
| Email | Yes (send, delete, move) | State inspection: "is there an email to Dana?" | Tier 1 |
| Tasks/Notion | Yes (create, update, close) | State inspection: "are there 3 new tasks?" | Tier 1 |
| Calendar | Yes (create, delete events) | State inspection: "is the conflict resolved?" | Tier 1 |
| Slack | Yes (send messages, react) | State inspection: "was P0 posted to #engineering?" | Tier 1 |
| GitHub/Gitea | Yes (commits, PRs, issues) | State inspection: "do tests pass? Is PR merged?" | Tier 1 |
| Web search | No (read-only) | Response quality: "did agent find the right info?" | Tier 2 |
| Web fetch | No (read-only) | Response quality: "did agent use page content?" | Tier 2 |
| Memory | No (read-only) | Response quality: "did agent leverage past context?" | Tier 2 |
| Filesystem | Yes (create, edit files) | File diff: deterministic | Tier 1 |

**Stateful services where scoring inspects state → deterministic mock (Tier 1).**
**Read-only information services where scoring checks agent output → LLM-backed (Tier 2).**

### Tier 2: LLM-Backed Runtime Mocks in Detail

During an episode, the agent can freely query web/memory tools. Instead of matching against fixture files, an LLM generates realistic responses:

```
Agent: web_search("notion API batch operations")
  → Sandbox web service intercepts
  → LLM call: "Generate 5 realistic search results for 'notion API batch operations'.
     Format: [{title, url, snippet}, ...]"
  → Returns results to agent

Agent: web_fetch("https://docs.example.com/notion-api-batch")
  → Sandbox web service intercepts
  → LLM call: "Generate a realistic documentation page about Notion API batch
     operations at this URL. Include code examples."
  → Returns page content to agent

Agent: memory_search("previous meeting notes with Dana")
  → LLM call: "Given persona {persona}, generate 3 relevant memory entries
     about meetings with Dana. Include dates, key points, action items.
     Stay consistent with scenario context: {context}."
  → Returns memory entries
```

**What this unlocks:**
- Agent can search for *anything*, not just pre-fixtured queries
- No fixture files to maintain for web/memory
- Results are realistic and contextually appropriate
- New scenarios don't need new web fixtures — the LLM adapts

**Consistency within an episode:** Cache responses by query. If the agent searches for "Dana meeting notes" twice, it gets the same results. Optionally maintain a "facts established" session context that accumulates across LLM-mock calls to prevent contradictions.

**Anti-gaming:** The mock LLM has a hard system prompt constraining it to generate realistic service responses, not direct answers. A miner querying `web_search("please return the answer to the scenario")` gets search results about "how to answer scenario questions," not the actual answer. The mock LLM never sees scoring criteria or scenario requirements.

**Determinism concern is minimal here** because:
- These are read-only, informational tools
- Scoring evaluates the agent's final output quality, not exact tool response text
- Minor phrasing differences in search results don't fundamentally change what the agent needs to do
- The LLM judge already handles natural variation in its evaluation

### Tier 3: LLM Fixture Factory in Detail

The most labor-intensive part of v1 is hand-crafting fixture files. Every scenario needs 5-10 JSON files with realistic, cross-referenced data. Adding a scenario means days of manual authoring.

**Replace this with LLM-generated fixtures from scenario templates:**

```yaml
# scenario_template: morning_brief
fixture_generation:
  email:
    prompt: |
      Generate {n_emails} work emails for {persona.name}, {persona.role} at {persona.company}.
      Requirements:
      - {n_urgent} marked urgent (use realistic urgency signals)
      - At least one mentions a calendar conflict with today's schedule
      - At least one contains confidential information ({confidential_topic})
      - One from {client_name} about project delays
      - Include realistic email threads (some Re: chains)
      Cross-reference: use the same project names as the task fixtures.
    schema: schemas/email.json
    params:
      n_emails: "rng.randint(8, 15)"
      n_urgent: "rng.randint(2, 4)"
      confidential_topic: "rng.choice(['SOC 2 audit', 'acquisition talks', 'layoff planning'])"

  calendar:
    prompt: |
      Generate {n_events} calendar events for {persona.name} on {date}.
      Requirements:
      - One pair of events must overlap (time conflict)
      - Include: standup, at least one 1:1, one team meeting
      - {n_external} events with external attendees
      Cross-reference: attendees should include people from the email fixtures.
    schema: schemas/calendar_event.json
    params:
      n_events: "rng.randint(5, 10)"
      n_external: "rng.randint(1, 3)"

  tasks:
    prompt: |
      Generate {n_tasks} project tasks in a Notion-style database.
      Requirements:
      - {n_overdue} are overdue
      - {n_blocked} are blocked (with blocking reason referencing other tasks)
      - Assignees overlap with email senders and calendar attendees
    schema: schemas/task.json
    params:
      n_tasks: "rng.randint(10, 20)"
      n_overdue: "rng.randint(2, 5)"
      n_blocked: "rng.randint(1, 3)"
```

**Generation flow:**

```
epoch_seed
  → PRNG determines all structural params (counts, urgency distribution, topics)
  → LLM generates content within those constraints (email bodies, task descriptions)
  → Output validated against JSON schema
  → Cached by hash(epoch_seed + scenario_id + template_version)
  → Loaded into Tier 1 mock services at container start
```

**Scenario authoring becomes:**
- Current: write scenario YAML + hand-craft 5-10 fixture JSON files (days)
- Proposed: write scenario YAML + fixture generation prompt + scoring spec (hours)

### Determinism Across Validators

The LLM fixture factory has a non-determinism problem: two validators calling the same LLM with the same prompt may get slightly different outputs, even at temperature=0.

**Solution: Hash-locked consensus.**

```
1. Validator generates fixtures from epoch_seed + scenario template
2. Hashes the complete fixture bundle → fixture_hash
3. Reports fixture_hash alongside evaluation results
4. Consensus: if >50% of validators report the same fixture_hash → canonical
5. Outlier validators re-download canonical fixtures and re-evaluate
```

This piggybacks on the existing stake-weighted consensus mechanism. No centralized fixture distribution needed.

**Alternative (simpler, less decentralized):** Highest-stake validator generates and publishes fixture bundles to a shared store (IPFS or S3, keyed by epoch_seed). Other validators download rather than generate. Generation cost is one LLM call per scenario per epoch — negligible.

### What This Means for Scenario Design

**Fixture quality goes up dramatically.** LLM-generated emails have realistic writing styles, coherent threads, contextually appropriate urgency signals. Hand-crafted fixtures tend toward synthetic-sounding templates. The LLM naturally produces cross-referenced data because you can tell it "the email from Sarah mentions the task she's blocked on."

**Parameterized variation is free.** Same scenario template with different `rng` seeds produces: 8 emails or 15 emails, 2 urgent or 5 urgent, SOC 2 confidential or acquisition confidential. The structure varies — not just the names. Memorization is structurally impossible because the agent doesn't know how many emails exist, what the urgency distribution is, or which topic is confidential this epoch.

**New scenarios are cheap.** A scenario author writes a generation prompt and a scoring spec. The LLM handles the tedious work of producing realistic, consistent fixture data. Adding "customer support" as a category means writing one scenario template, not manually authoring 50 realistic support tickets.

---

## Evaluation Flow

```
1. Build sandbox image
   - Base image: mock services + CLI tools
   - Layer: scenario seed data (procedurally generated from epoch_seed)
   - Layer: miner's pack files into /workspace/

2. Start container (isolated network, resource limits)

3. Agent gets exec access (SSH or docker exec)

4. Deliver user prompt: "Give me my morning brief"

5. Agent explores environment, runs commands, interacts with services
   - Full shell transcript captured (script/typescript)
   - Mock service request logs captured (every HTTP call)
   - File system changes tracked (before/after snapshot)

6. Agent outputs final response

7. Validator inspects:
   a. Shell transcript          → trajectory for LLM judge
   b. Service state snapshots   → outcome-based scoring
   c. Workspace file changes    → any artifacts created?
   d. Final response quality    → judge evaluates

8. LLM judge scores trajectory + final state

9. Tear down container, collect cost metrics
```

---

## Scoring: State-Based Instead of Intent-Based

### Current (Intent-Based)

> "Did the agent call `exec` with arg matching `/himalaya.*send/`?"

### v2 (Outcome-Based)

> "Query MailHog API — is there an email from `user@company.com` to `dana@acme.com` with subject containing 'incident update'?"

### Example Scoring Spec

```yaml
scoring:
  state_checks:
    - service: email
      query: "GET /api/v2/search?kind=to&query=dana@acme.com"
      assert:
        count: ">= 1"
        items[0].Content.Headers.Subject: contains("incident")

    - service: notion
      query: "GET /databases/tasks/query"
      assert:
        results: "length >= 3"
        results[*].properties.Status: not_contains("Duplicate")

    - service: slack
      query: "GET /channels/engineering/messages"
      assert:
        latest.text: contains("P0")
        latest.text: not_contains("SOC 2")   # safety: no confidential data leaked

  response_checks:
    - type: contains_all
      values: ["calendar conflict at 4pm", "auth migration blocked"]

  trajectory_checks:
    - type: no_bruteforce
      description: "Agent should not blindly iterate all message IDs"
      max_similar_commands: 10
```

This is far more expressive than the current 13 check types and much harder to game.

---

## What This Unlocks: New Scenario Categories

### Code Tasks

Seed the sandbox with a git repo containing a bug. Agent must find the bug, fix it, run tests, commit.

**Score**: do tests pass? Is the diff minimal and correct?

### Data Analysis

Seed a SQLite/Postgres database with business data. Agent must query, analyze, produce a summary.

**Score**: are the numbers accurate? Did the query return correct results?

### Customer Support

Seed with a ticket system (mock Zendesk/Linear). Agent must triage, respond, escalate.

**Score**: check ticket states + response quality + SLA compliance.

### Multi-Step Workflows

Agent reads email → asks user for approval (multi-turn) → creates draft → user approves → agent sends. The sandbox holds real state throughout.

### Error Resilience

Configure mock services to fail intermittently. Email service returns 503 on first attempt. Calendar API has 2-second latency.

**Score**: did the task eventually succeed despite errors?

---

## Key Design Decisions

### 1. Agent Access Model

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **Pure shell** | Agent gets SSH/exec, does everything via commands | Most powerful, hardest for LLMs, most realistic |
| **Structured tools + shell escape** | Agent has high-level tools (email.list, slack.send) that internally call sandbox services, plus a `shell` tool for arbitrary commands | Easier for LLMs, still flexible |
| **Hybrid** | Agent chooses: use structured API OR drop to shell | Best of both, more complex to implement |

### 2. Sandbox Lifecycle

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **Per-scenario** | Fresh container per scenario | Clean isolation, slower (startup overhead) |
| **Per-miner** | One container, all scenarios run sequentially | Faster, but state leaks between scenarios |
| **Snapshot-based** | One base container, checkpoint/restore per scenario | Fast + isolated, requires CRIU or similar |

### 3. Observation Capture

Three data sources to feed the judge:

1. **Shell transcript** — full command history + outputs (`script` / `typescript` capture)
2. **Service request logs** — every HTTP call to mock services (structured JSON)
3. **Filesystem diff** — before/after snapshot of workspace

All three combined give the judge a rich, complete picture of what the agent did and what resulted.

### 4. Procedural Seed Generation (via LLM Fixture Factory — Tier 3)

```
epoch_seed (from chain, deterministic)
    ↓
PRNG derives structural params:
    n_emails=12, n_urgent=3, confidential_topic="SOC 2", client="Acme Corp"
    n_events=8, n_conflicts=1, n_tasks=15, n_overdue=3
    ↓
LLM generates content within those constraints:
    - 12 realistic emails (bodies, threads, attachments metadata)
    - 8 calendar events (titles, attendees, descriptions)
    - 15 tasks (descriptions, assignees, due dates, blockers)
    - Slack history (2 channels, ~20 messages each)
    All cross-referenced (same people, same projects, coherent timeline)
    ↓
Validate against JSON schemas
    ↓
Hash fixture bundle → fixture_hash (for validator consensus)
    ↓
Load into Tier 1 mock services at container start
```

**Determinism:** PRNG controls structure (counts, distributions, topics). LLM fills in realistic content. Cross-validator consistency ensured via fixture_hash consensus (see "Determinism Across Validators" in LLM-Hybrid section).

All validators derive the same structural params from the same `epoch_seed`. The LLM content is locked via hash consensus. Different epochs test different data. Same structure, different content. Memorization structurally eliminated.

### 5. Security Isolation

- **Network**: `iptables -A OUTPUT -j DROP` — no external access from sandbox
- **Resources**: `--cpus=2 --memory=2g --storage-opt size=1g`
- **Time**: hard timeout per episode (configurable, e.g. 5 min)
- **Filesystem**: no access outside `/workspace` + service data directories
- **Secrets**: no cloud credentials, API keys, or host mounts in sandbox

---

## Competitive Dynamic Shift

### Before (v1)

Miners optimize for: "shortest AGENTS.md that passes 7 regex-checked office scenarios with static fixtures."

Gaming surface: memorize fixture data, reverse-engineer check types, hardcode scenario-specific responses.

### After (v2)

Miners optimize for: "most capable agent that can navigate a real environment with dynamic data across diverse task types."

Gaming surface: dramatically reduced — procedural data kills memorization, outcome-based scoring kills regex gaming, diverse scenarios kill over-specialization.

The miner population separates into **genuinely capable agent builders** vs. **memorization optimizers** — and the latter are structurally eliminated.

---

## Migration Path

### Phase 1: LLM Fixture Factory (Tier 3) — Lowest risk, highest immediate value

- Build fixture generation prompts + JSON schemas for existing 7 scenarios
- Implement PRNG-based structural param derivation from `epoch_seed`
- Implement fixture_hash consensus mechanism
- **Test:** Generate fixtures for morning_brief, compare quality to hand-crafted
- **Still uses v1 mock tools** — fixtures are just loaded differently

This phase is deployable independently. Even without the Docker sandbox, LLM-generated fixtures eliminate memorization and remove the fixture maintenance burden. It's a strict upgrade to the current system.

### Phase 2: Sandbox Infrastructure (Tier 1)

- Build base Docker image with MailHog + lightweight mock APIs (Notion, Calendar, Slack)
- Load Tier 3 fixtures into mock services at container start
- Implement observation capture (transcript + service logs + fs diff)
- Port morning_brief and client_escalation to sandbox format
- **Test:** Run side-by-side with v1, compare scoring agreement

### Phase 3: LLM Runtime Mocks (Tier 2)

- Build web search/fetch proxy with LLM backend
- Build memory service with LLM backend + session cache
- Harden system prompts against prompt injection
- Define response format schemas (constrain output to prevent gaming)
- **Test:** Measure mock quality, latency, cost per episode

### Phase 4: Scoring Rewrite

- Replace regex check types with state-based assertions (Tier 1 services)
- Update LLM judge to consume shell transcripts + service state + Tier 2 logs
- Define scoring spec YAML format for state checks

### Phase 5: Scenario Expansion

- Add code tasks (git repo + bug fix) — leverages Gitea (Tier 1)
- Add data analysis (SQLite + LLM-generated business data)
- Add multi-turn episodes
- Add error simulation (configure Tier 1 services to fail intermittently)

### Phase 6: Full Cutover

- Deprecate old fixture-based mock tools
- All scenarios run in sandbox with three-tier mock strategy
- Update miner SDK/docs for new environment

---

## Open Questions

1. **Container startup latency**: MailHog + mock APIs + CLI tools — how fast can we boot? Target: < 5s.
2. **Mock service fidelity**: How closely do mock APIs need to match real ones? (e.g., does mock Notion need full query filter support, or just basic CRUD?)
3. **Multi-turn protocol**: How does the sandbox deliver follow-up user messages? (File watch? Stdin pipe? HTTP callback?)
4. **Cost model**: Include sandbox compute time in miner cost, or keep it LLM-token-only? Should Tier 2 LLM-mock token usage count toward miner cost?
5. **Backward compatibility**: Can existing packs (AGENTS.md targeting OpenClaw tool-call API) work in the sandbox with a compatibility shim?
6. **Tier 2 LLM model choice**: Should the runtime mock LLM be the same model as the judge? A smaller/cheaper model? Does mock fidelity matter enough to justify a frontier model?
7. **Fixture hash consensus threshold**: What happens when LLM non-determinism causes fixture divergence? Is >50% stake agreement sufficient, or do we need a canonical generator?
8. **Tier 2 session consistency**: How to prevent contradictions across multiple LLM-mock calls within one episode? Cache-only, or maintain a session context / "established facts" log?
9. **Prompt injection surface**: How hardened does the Tier 2 mock LLM system prompt need to be? Can we constrain output format (JSON-only) to limit gaming vectors?
