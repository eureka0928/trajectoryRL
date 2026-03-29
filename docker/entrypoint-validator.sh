#!/usr/bin/env bash
# TrajectoryRL Validator — All-in-One Entrypoint
#
# Manages three processes inside a single container:
#   1. mock-tools   (Python/FastAPI on port 3001)
#   2. OpenClaw     (Node.js gateway on port 18789)
#   3. validator    (Python, foreground — PID 1 via exec)
#
# On startup, runs init_workspace.py once to set up fixtures + config.

set -euo pipefail

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%S%z') [entrypoint] $*"; }

log "TrajectoryRL Validator (all-in-one)"

# ── 1. Environment setup ────────────────────────────────────────
# Paths for init_workspace.py (override legacy Docker volume mount defaults)
export SCENARIOS_DIR="${SCENARIOS_DIR:-/app/clawbench/scenarios}"
export FIXTURES_DIR="${FIXTURES_DIR:-/app/clawbench/fixtures}"
export WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
export WORKSPACE_PATH="${WORKSPACE_PATH:-$WORKSPACE_DIR}"
export CONFIG_DIR="${CONFIG_DIR:-/app/clawbench/config}"
# Do NOT set OPENCLAW_HOME here.  OpenClaw uses OPENCLAW_HOME to
# resolve its config directory ($OPENCLAW_HOME/.openclaw/).  Setting it
# to /openclaw-home makes the gateway look for config in
# /openclaw-home/.openclaw/ instead of $HOME/.openclaw/ (/root/.openclaw/).
# init_workspace.py writes to OPENCLAW_CONFIG_DIR which defaults to
# $HOME/.openclaw/ — matching where the gateway reads.
export OPENCLAW_CONFIG_DIR="${OPENCLAW_CONFIG_DIR:-/root/.openclaw}"

# Do NOT set OPENAI_API_KEY / OPENAI_BASE_URL here.  OpenClaw's
# provider-detection logic maps OPENAI_API_KEY to the "openai" provider
# and overrides the model to anthropic/claude, ignoring the providers
# section in openclaw.json.  The generated config already contains the
# API key and base URL per provider (substituted from CLAWBENCH_LLM_*
# env vars by init_workspace.py), so OPENAI_* env vars are not needed.
export OPENCLAW_GATEWAY_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-sandbox-token-12345}"

# All-in-one defaults (no Docker port mapping)
export OPENCLAW_URL="${OPENCLAW_URL:-http://localhost:18789}"
export MOCK_TOOLS_URL="${MOCK_TOOLS_URL:-http://localhost:3001}"

# ── 2. Init workspace (one-shot) ─────────────────────────────────
log "Initializing workspace..."
mkdir -p "$WORKSPACE_DIR" "$OPENCLAW_CONFIG_DIR"
python /app/clawbench/scripts/init_workspace.py

chmod -R 777 "$WORKSPACE_DIR"
log "Workspace ready"

# ── 3. Start mock-tools server (background) ─────────────────────
# Logs are redirected to separate files so they don't pollute the
# main validator output.  Inspect these files when debugging tool calls.
SUBMODULE_LOG_DIR="${LOG_DIR:-/app/logs}/submodules"
mkdir -p "$SUBMODULE_LOG_DIR"

log "Starting mock-tools on port 3001..."
log "  mock-tools logs → $SUBMODULE_LOG_DIR/mock-tools.log"
python -m mock_tools.server >> "$SUBMODULE_LOG_DIR/mock-tools.log" 2>&1 &
MOCK_PID=$!

for i in $(seq 1 30); do
    if python -c "import urllib.request; urllib.request.urlopen('http://localhost:3001/health')" 2>/dev/null; then
        log "mock-tools ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log "ERROR: mock-tools failed to start within 30s"
        exit 1
    fi
    sleep 1
done

# ── 4. Start OpenClaw gateway (background) ──────────────────────
log "Starting OpenClaw gateway on port 18789..."
log "  openclaw logs → $SUBMODULE_LOG_DIR/openclaw-gateway.log"
cd /app/openclaw
node dist/index.js gateway --allow-unconfigured --bind loopback >> "$SUBMODULE_LOG_DIR/openclaw-gateway.log" 2>&1 &
OPENCLAW_PID=$!
cd /app

for i in $(seq 1 60); do
    if curl -sf http://localhost:18789/health >/dev/null 2>&1; then
        log "OpenClaw gateway ready"
        break
    fi
    if [ "$i" -eq 60 ]; then
        log "ERROR: OpenClaw gateway failed to start within 60s"
        exit 1
    fi
    sleep 1
done

# ── 5. Signal handling ──────────────────────────────────────────
cleanup() {
    log "Shutting down..."
    kill "$MOCK_PID" "$OPENCLAW_PID" 2>/dev/null || true
    wait "$MOCK_PID" "$OPENCLAW_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── 6. Start validator (foreground) ─────────────────────────────
log "Starting validator..."
exec python -u neurons/validator.py "$@"
