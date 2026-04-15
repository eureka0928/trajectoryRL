"""End-to-end Season 1 validator evaluation test.

Runs the full flow with real Docker containers and real LLM API calls:
  1. Fixture generation via sandbox image CLI
  2. Sandbox container startup (mock services)
  3. Harness agent container (Hermes, real LLM calls)
  4. LLM judge scoring via sandbox image CLI
  5. Split-half delta computation

Requirements:
  - Docker daemon running
  - Images cached: ghcr.io/trajectoryrl/trajrl-bench:latest,
    ghcr.io/trajectoryrl/hermes-agent:latest (or nousresearch/hermes-agent)
  - CLAWBENCH_LLM_API_KEY set (OpenRouter or compatible)
  - CLAWBENCH_LLM_BASE_URL set
  - CLAWBENCH_DEFAULT_MODEL set

Run:
  CLAWBENCH_LLM_API_KEY=sk-or-... python -m pytest tests/test_s1_e2e.py -v -s

This test takes ~5-10 minutes (4 episodes × ~1-2 min each + scoring).
Mark as slow so normal test runs skip it.
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Skip entire module if Docker or API key not available
docker = pytest.importorskip("docker")

LLM_API_KEY = os.environ.get("CLAWBENCH_LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("CLAWBENCH_LLM_BASE_URL", "https://openrouter.ai/api/v1")
_raw_model = os.environ.get("CLAWBENCH_DEFAULT_MODEL", "z-ai/glm-5.1")
# Strip "openrouter/" prefix — the sandbox scorer calls OpenRouter directly
LLM_MODEL = _raw_model.removeprefix("openrouter/")

SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "ghcr.io/trajectoryrl/trajrl-bench:latest")
HARNESS_IMAGE = os.environ.get("HARNESS_IMAGE", "ghcr.io/trajectoryrl/hermes-agent:latest")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _skip_if_no_docker():
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        pytest.skip("Docker daemon not available")


def _skip_if_no_api_key():
    if not LLM_API_KEY:
        pytest.skip("CLAWBENCH_LLM_API_KEY not set")


# ---------------------------------------------------------------------------
# Test SKILL.md packs (one good, one baseline)
# ---------------------------------------------------------------------------

SKILLED_SKILL_MD = """\
# Incident Response Agent

## Approach
1. Start by reading INSTRUCTION.md to understand the task
2. Hit /health to discover available services
3. Read all emails via /api/v2/messages to get full context
4. Check Slack channels for team communications
5. Review calendar for scheduling constraints
6. Check Gitea for related issues/PRs

## Decision Framework
- Prioritize by severity: P0 (security/outage) > P1 (degraded) > P2 (minor)
- Always acknowledge urgent items within the first action
- Cross-reference information across services before concluding
- If conflicting information found, flag it explicitly

## Communication Style
- Use structured, actionable summaries
- Lead with the most critical finding
- Include specific evidence (email subjects, Slack messages, issue numbers)
- Never fabricate information — only report what the data shows

## Learning Strategy
After each episode, write key patterns to /workspace/learned/notes.md:
- What data sources were most useful
- What patterns indicated severity
- What cross-references revealed hidden issues
"""

BASELINE_SKILL_MD = """\
# Agent
Do your best with the task given.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    """Build a ValidatorConfig for S1 testing."""
    from trajectoryrl.utils.config import ValidatorConfig

    # clawbench_path must exist (validator config validates it)
    clawbench_path = Path("/tmp/clawbench-test")
    clawbench_path.mkdir(parents=True, exist_ok=True)
    (clawbench_path / "scenarios").mkdir(parents=True, exist_ok=True)

    return ValidatorConfig(
        wallet_name="test",
        wallet_hotkey="test",
        netuid=11,
        network="test",
        evaluation_harness="trajrl-bench",
        sandbox_image=SANDBOX_IMAGE,
        harness_image=HARNESS_IMAGE,
        clawbench_api_key=LLM_API_KEY,
        clawbench_base_url=LLM_BASE_URL,
        clawbench_default_model=LLM_MODEL,
        sandbox_num_episodes=4,
        sandbox_timeout_per_episode=300,  # 5 min per episode
        clawbench_path=clawbench_path,
        ema_state_path=Path("/tmp/trajrl-test-ema.json"),
        winner_state_path=Path("/tmp/trajrl-test-winner.json"),
        pack_cache_dir=Path("/tmp/trajrl-test-packs"),
    )


# ---------------------------------------------------------------------------
# Phase tests (incremental — each phase builds on the previous)
# ---------------------------------------------------------------------------

class TestS1Phase1Fixtures:
    """Phase 1: Fixture generation via sandbox image CLI."""

    def test_scenarios_query(self):
        """Sandbox image reports available scenarios and version."""
        _skip_if_no_docker()
        client = docker.from_env()

        output = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-m", "trajrl_bench.cli", "scenarios"],
            entrypoint="",
            remove=True, stdout=True, stderr=True,
        )
        data = json.loads(output.decode())

        assert "version" in data
        assert "scenarios" in data
        assert "incident_response" in data["scenarios"]
        logger.info("Sandbox version: %s, scenarios: %s",
                    data["version"], data["scenarios"])

    def test_fixture_generation(self):
        """Generate fixtures for 4 episodes — deterministic from seed."""
        _skip_if_no_docker()
        client = docker.from_env()

        output = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-m", "trajrl_bench.cli", "generate",
                     "--seed", "42", "--salt", "test-validator",
                     "--episodes", "4"],
            entrypoint="",
            remove=True, stdout=True, stderr=True,
        )
        data = json.loads(output.decode())

        assert data["scenario"] in ("incident_response", "morning_brief")
        assert "world" in data
        assert data["world"]["company"]  # non-empty company name
        assert len(data["episodes"]) == 4

        for i, ep in enumerate(data["episodes"]):
            assert "instruction_md" in ep, f"Episode {i} missing instruction"
            assert "fixtures" in ep, f"Episode {i} missing fixtures"
            assert len(ep["instruction_md"]) > 50, f"Episode {i} instruction too short"
            fixtures = ep["fixtures"]
            assert "inbox" in fixtures, f"Episode {i} missing inbox fixtures"

        logger.info("Fixtures generated: scenario=%s, company=%s",
                    data["scenario"], data["world"]["company"])

    def test_fixture_determinism(self):
        """Same seed+salt → same fixtures."""
        _skip_if_no_docker()
        client = docker.from_env()

        outputs = []
        for _ in range(2):
            output = client.containers.run(
                SANDBOX_IMAGE,
                command=["python", "-m", "trajrl_bench.cli", "generate",
                         "--seed", "99", "--salt", "determinism-test",
                         "--episodes", "2"],
                entrypoint="",
                remove=True, stdout=True, stderr=True,
            )
            outputs.append(json.loads(output.decode()))

        assert outputs[0]["scenario"] == outputs[1]["scenario"]
        assert outputs[0]["world"] == outputs[1]["world"]
        assert outputs[0]["episodes"][0]["instruction_md"] == outputs[1]["episodes"][0]["instruction_md"]
        logger.info("Determinism verified: scenario=%s", outputs[0]["scenario"])


class TestS1Phase2Sandbox:
    """Phase 2: Sandbox container startup and mock services."""

    def test_sandbox_lifecycle(self):
        """Start sandbox, verify health, load fixtures, capture state."""
        _skip_if_no_docker()
        client = docker.from_env()

        # Generate fixtures first
        gen_output = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-m", "trajrl_bench.cli", "generate",
                     "--seed", "42", "--salt", "test",
                     "--episodes", "1"],
            entrypoint="",
            remove=True, stdout=True, stderr=True,
        )
        gen_data = json.loads(gen_output.decode())

        # Start sandbox container
        network = None
        sandbox = None
        try:
            network = client.networks.create(
                "test_e2e_sandbox", driver="bridge", internal=True)

            sandbox = client.containers.run(
                SANDBOX_IMAGE, detach=True, network=network.name,
                name="test_e2e_sandbox_container",
                environment={"SSH_PUBLIC_KEY": "ssh-ed25519 AAAA test@test"},
                mem_limit="2g",
            )

            # Wait for health
            healthy = False
            for _ in range(30):
                time.sleep(1)
                try:
                    code, out = sandbox.exec_run(
                        ["sh", "-c", "curl -s http://localhost:8090/health"])
                    if code == 0 and out:
                        health = json.loads(out.decode())
                        if health.get("status") == "ok":
                            healthy = True
                            break
                except Exception:
                    pass
            assert healthy, "Sandbox mock services failed to start"
            logger.info("Sandbox healthy")

            # Reset and load fixtures
            sandbox.exec_run(
                ["sh", "-c", "curl -s -X POST http://localhost:8090/reset"])

            from trajectoryrl.utils.sandbox_harness import _put_file
            fixtures_json = json.dumps(gen_data["episodes"][0]["fixtures"])
            _put_file(sandbox, "/tmp/fixtures.json", fixtures_json)
            code, out = sandbox.exec_run(
                ["sh", "-c",
                 "curl -s -X POST http://localhost:8090/load_fixtures "
                 "-H 'Content-Type: application/json' -d @/tmp/fixtures.json"])
            assert code == 0, f"Failed to load fixtures: {out.decode()}"
            logger.info("Fixtures loaded")

            # Capture state
            code, state_raw = sandbox.exec_run(
                ["sh", "-c", "curl -s http://localhost:8090/state"])
            assert code == 0
            state = json.loads(state_raw.decode())
            assert "emails" in state or "inbox" in state or isinstance(state, dict)
            logger.info("State captured: %d keys", len(state))

        finally:
            if sandbox:
                try:
                    sandbox.stop(timeout=3)
                    sandbox.remove(force=True)
                except Exception:
                    pass
            if network:
                try:
                    network.remove()
                except Exception:
                    pass


class TestS1Phase3Harness:
    """Phase 3: TrajectorySandboxHarness integration — full eval.

    This is the real deal: generates fixtures, spins up containers,
    runs a Hermes agent with real LLM calls, scores with LLM judge.
    """

    @pytest.mark.asyncio
    async def test_harness_pull_and_version(self):
        """Harness can pull images and query sandbox version."""
        _skip_if_no_docker()
        config = _make_config()
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness
        harness = TrajectorySandboxHarness(config)

        await harness.pull_latest()
        assert harness.sandbox_version != "unknown"
        assert len(harness.sandbox_scenarios) > 0
        assert "incident_response" in harness.sandbox_scenarios
        logger.info("Version: %s, Scenarios: %s",
                    harness.sandbox_version, harness.sandbox_scenarios)

    @pytest.mark.asyncio
    async def test_full_evaluation_skilled(self):
        """Full S1 eval with a skilled SKILL.md — expect score > 0."""
        _skip_if_no_docker()
        _skip_if_no_api_key()
        config = _make_config()
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness
        harness = TrajectorySandboxHarness(config)

        t0 = time.time()
        result = await harness.evaluate_miner(
            skill_md=SKILLED_SKILL_MD,
            epoch_seed=12345,
            pack_hash="e2e_test_skilled",
            validator_salt="e2e_test_salt",
        )
        elapsed = time.time() - t0

        # Log everything
        logger.info("=== SKILLED SKILL.MD RESULT ===")
        logger.info("Score: %.4f", result.score)
        logger.info("Mean quality: %.4f", result.mean_quality)
        logger.info("Episode qualities: %s", result.episode_qualities)
        logger.info("Delta: %.4f", result.delta)
        logger.info("Learning bonus: %.4f", result.learning_bonus)
        logger.info("Early mean: %.4f", result.early_mean)
        logger.info("Late mean: %.4f", result.late_mean)
        logger.info("Scenario: %s", result.scenario_name)
        logger.info("Error: %s", result.error)
        logger.info("Elapsed: %.1fs", elapsed)

        # Assertions
        assert result.error is None, f"Evaluation failed: {result.error}"
        assert len(result.episode_qualities) == 4, \
            f"Expected 4 episodes, got {len(result.episode_qualities)}"
        assert result.score >= 0.0, "Score must be non-negative"
        assert result.mean_quality >= 0.0, "Mean quality must be non-negative"
        assert result.scenario_name in ("incident_response", "morning_brief")

        # A skilled pack should get a non-zero score
        assert result.score > 0.0, \
            f"Skilled pack scored 0.0 — something is wrong. Episodes: {result.episode_qualities}"

        # Each episode quality should be 0.0-1.0
        for i, q in enumerate(result.episode_qualities):
            assert 0.0 <= q <= 1.0, f"Episode {i} quality {q} out of range"

    @pytest.mark.asyncio
    async def test_full_evaluation_baseline(self):
        """Full S1 eval with a minimal baseline SKILL.md."""
        _skip_if_no_docker()
        _skip_if_no_api_key()
        config = _make_config()
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness
        harness = TrajectorySandboxHarness(config)

        t0 = time.time()
        result = await harness.evaluate_miner(
            skill_md=BASELINE_SKILL_MD,
            epoch_seed=12345,
            pack_hash="e2e_test_baseline",
            validator_salt="e2e_test_salt",
        )
        elapsed = time.time() - t0

        logger.info("=== BASELINE SKILL.MD RESULT ===")
        logger.info("Score: %.4f", result.score)
        logger.info("Mean quality: %.4f", result.mean_quality)
        logger.info("Episode qualities: %s", result.episode_qualities)
        logger.info("Delta: %.4f", result.delta)
        logger.info("Elapsed: %.1fs", elapsed)

        assert result.error is None, f"Evaluation failed: {result.error}"
        assert len(result.episode_qualities) == 4

    @pytest.mark.asyncio
    async def test_skill_quality_gap(self):
        """Skilled SKILL.md should score higher than baseline.

        This is the core thesis of S1: SKILL.md quality drives score.
        The pressure test on Apr 13 showed +39-50pp improvement.
        """
        _skip_if_no_docker()
        _skip_if_no_api_key()
        config = _make_config()
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness

        # Use same seed for both so fixtures are identical
        harness = TrajectorySandboxHarness(config)

        skilled_result = await harness.evaluate_miner(
            skill_md=SKILLED_SKILL_MD,
            epoch_seed=77777,
            pack_hash="e2e_gap_skilled",
            validator_salt="e2e_gap_salt",
        )

        baseline_result = await harness.evaluate_miner(
            skill_md=BASELINE_SKILL_MD,
            epoch_seed=77777,
            pack_hash="e2e_gap_baseline",
            validator_salt="e2e_gap_salt",
        )

        logger.info("=== QUALITY GAP TEST ===")
        logger.info("Skilled: score=%.4f, mean_q=%.4f, episodes=%s",
                    skilled_result.score, skilled_result.mean_quality,
                    skilled_result.episode_qualities)
        logger.info("Baseline: score=%.4f, mean_q=%.4f, episodes=%s",
                    baseline_result.score, baseline_result.mean_quality,
                    baseline_result.episode_qualities)
        logger.info("Gap: %.4f", skilled_result.score - baseline_result.score)

        # Skilled should outperform baseline
        assert skilled_result.score >= baseline_result.score, \
            (f"Skilled ({skilled_result.score:.4f}) should beat "
             f"baseline ({baseline_result.score:.4f})")


class TestS1Phase4ValidatorDispatch:
    """Phase 4: Validator-level dispatch (S1 vs v4.0)."""

    def test_pack_content_routing(self):
        """Pack with SKILL.md routes to S1, AGENTS.md routes to v4.0."""
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness

        # SKILL.md extraction
        s1_pack = {"schema_version": 1, "files": {"SKILL.md": SKILLED_SKILL_MD}}
        assert TrajectorySandboxHarness.extract_skill_md(s1_pack) == SKILLED_SKILL_MD

        # No SKILL.md → returns None
        v4_pack = {"schema_version": 1, "files": {"AGENTS.md": "# Policy"}}
        assert TrajectorySandboxHarness.extract_skill_md(v4_pack) is None

        # Case-insensitive
        lower_pack = {"schema_version": 1, "files": {"skill.md": "# test"}}
        assert TrajectorySandboxHarness.extract_skill_md(lower_pack) == "# test"

    def test_scoring_version_from_sandbox(self):
        """Scoring version derived from sandbox major version."""
        from trajectoryrl.utils.sandbox_harness import TrajectorySandboxHarness
        config = _make_config()
        harness = TrajectorySandboxHarness(config)

        harness.sandbox_version = "1.0.0"
        assert harness.scoring_version == 1

        harness.sandbox_version = "2.3.1"
        assert harness.scoring_version == 2

        harness.sandbox_version = "unknown"
        assert harness.scoring_version == 1  # fallback

    def test_split_half_delta_computation(self):
        """Verify split-half delta math including anti-sandbagging."""
        from trajectoryrl.utils.sandbox_harness import _SessionResult, _EpisodeResult

        # Normal case: improvement across episodes
        session = _SessionResult(episodes=[
            _EpisodeResult(0, quality=0.4),
            _EpisodeResult(1, quality=0.5),
            _EpisodeResult(2, quality=0.7),
            _EpisodeResult(3, quality=0.8),
        ])
        session.compute_scores()
        assert session.early_mean == pytest.approx(0.45)
        assert session.late_mean == pytest.approx(0.75)
        assert session.delta == pytest.approx(0.30)
        assert session.learning_bonus == pytest.approx(0.15)
        expected = 0.6 * (1 + 0.15)  # mean * (1 + bonus)
        assert session.final_score == pytest.approx(expected)

        # Anti-sandbagging: low early + high delta → zeroed
        session_sandbag = _SessionResult(episodes=[
            _EpisodeResult(0, quality=0.1),
            _EpisodeResult(1, quality=0.1),
            _EpisodeResult(2, quality=0.8),
            _EpisodeResult(3, quality=0.9),
        ])
        session_sandbag.compute_scores()
        assert session_sandbag.early_mean == pytest.approx(0.1)
        assert session_sandbag.delta == 0.0  # zeroed by guard
        assert session_sandbag.learning_bonus == 0.0
        assert session_sandbag.final_score == pytest.approx(0.475)  # just mean

        # Flat performance: no bonus
        session_flat = _SessionResult(episodes=[
            _EpisodeResult(0, quality=0.6),
            _EpisodeResult(1, quality=0.6),
            _EpisodeResult(2, quality=0.6),
            _EpisodeResult(3, quality=0.6),
        ])
        session_flat.compute_scores()
        assert session_flat.delta == pytest.approx(0.0)
        assert session_flat.learning_bonus == 0.0
        assert session_flat.final_score == pytest.approx(0.6)

        # Decline: negative delta → no bonus (max(0, delta))
        session_decline = _SessionResult(episodes=[
            _EpisodeResult(0, quality=0.8),
            _EpisodeResult(1, quality=0.9),
            _EpisodeResult(2, quality=0.4),
            _EpisodeResult(3, quality=0.3),
        ])
        session_decline.compute_scores()
        assert session_decline.delta < 0
        assert session_decline.learning_bonus == 0.0
        assert session_decline.final_score == pytest.approx(0.6)  # just mean


class TestS1Phase5ScoringIntegration:
    """Phase 5: LLM judge scoring via sandbox image CLI."""

    def test_score_command_with_mock_data(self):
        """Run the score CLI with synthetic data to verify it works."""
        _skip_if_no_docker()
        _skip_if_no_api_key()
        import tempfile

        client = docker.from_env()

        # Generate real fixtures first
        gen_output = client.containers.run(
            SANDBOX_IMAGE,
            command=["python", "-m", "trajrl_bench.cli", "generate",
                     "--seed", "42", "--salt", "score-test",
                     "--episodes", "1"],
            entrypoint="",
            remove=True, stdout=True, stderr=True,
        )
        gen_data = json.loads(gen_output.decode())

        # Create synthetic but plausible transcript
        transcript = (
            "Reading INSTRUCTION.md...\n"
            "Checking /health endpoint...\n"
            "Services available. Reading emails...\n"
            "Found 3 urgent emails. Checking Slack channels...\n"
            "P0 incident reported in #incidents. Prioritizing response.\n"
            "Sending acknowledgement to reporter. Creating Notion task.\n"
            "Cross-referencing with Gitea issues...\n"
            "Summary: Critical auth service outage, 3 teams affected.\n"
        )

        # Minimal mock state
        mock_state = {
            "emails": [{"subject": "test", "read": True}],
            "slack_messages": [{"channel": "incidents", "text": "P0 auth outage"}],
            "tasks": [{"title": "Investigate auth outage", "status": "open"}],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write inputs
            with open(f"{tmpdir}/world.json", "w") as f:
                json.dump(gen_data["world"], f)
            with open(f"{tmpdir}/episode.json", "w") as f:
                json.dump(gen_data["episodes"][0], f)
            with open(f"{tmpdir}/transcript.txt", "w") as f:
                f.write(transcript)
            with open(f"{tmpdir}/state.json", "w") as f:
                json.dump(mock_state, f)

            # Run scorer
            score_output = client.containers.run(
                SANDBOX_IMAGE,
                command=[
                    "python", "-m", "trajrl_bench.cli", "score",
                    "--world", "/data/world.json",
                    "--episode", "/data/episode.json",
                    "--transcript", "/data/transcript.txt",
                    "--state", "/data/state.json",
                    "--scenario", gen_data["scenario"],
                ],
                entrypoint="",
                environment={
                    "LLM_API_KEY": LLM_API_KEY,
                    "LLM_BASE_URL": LLM_BASE_URL,
                    "LLM_MODEL": LLM_MODEL,
                },
                volumes={tmpdir: {"bind": "/data", "mode": "ro"}},
                remove=True, stdout=True, stderr=True,
                mem_limit="2g",
            )

            score_data = json.loads(score_output.decode())
            logger.info("Score result: %s", json.dumps(score_data, indent=2)[:500])

            assert "quality" in score_data
            assert 0.0 <= score_data["quality"] <= 1.0
            logger.info("Score: %.4f", score_data["quality"])
