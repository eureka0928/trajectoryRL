"""Integration tests for the default pack generator flow.

Tests the full _run_default cycle: generate → build → validate → upload → submit
with mocked external dependencies (Anthropic API, S3, bittensor).
"""

import asyncio
import json
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from trajectoryrl.utils.config import MinerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_AGENTS_MD = """\
# Workplace Assistant Policy

## Core Principles
- Always read all available context before acting
- Acknowledge receipt of requests immediately
- Escalate urgent items without delay
- Use structured, actionable formatting
- Never fabricate information

## Communication
- Professional, helpful tone
- Concise but thorough
- Follow up proactively

## Safety
- Apply approval gates for sensitive actions
- Keep confidential information in appropriate channels
- Prioritize correctness over speed
"""

FAKE_AGENTS_MD_V2 = FAKE_AGENTS_MD + "\n## Improved\n- Better escalation handling\n"


def _make_config(**overrides):
    defaults = dict(
        wallet_name="miner",
        wallet_hotkey="default",
        netuid=11,
        network="test",
        check_interval=1,
        log_level="WARNING",
        anthropic_api_key="sk-ant-test-key",
        generator_model="claude-sonnet-4-5-20250929",
        s3_bucket="test-bucket",
        s3_key="pack.json",
        s3_region="us-east-1",
        pack_url="",
    )
    defaults.update(overrides)
    return MinerConfig(**defaults)


def _fake_pack_hash(agents_md):
    """Compute what the pack hash will be for a given AGENTS.md.

    Mirrors generate_agents_md's .strip() so the hash matches
    what _run_default will compute after LLM generation.
    """
    from trajectoryrl.base.miner import TrajectoryMiner
    pack = TrajectoryMiner.build_pack(agents_md=agents_md.strip())
    return TrajectoryMiner.compute_pack_hash(pack)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPackGenerator:
    """Tests for generate_agents_md()."""

    def test_generate_fresh(self):
        """First call (no previous) generates a new AGENTS.md."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=FAKE_AGENTS_MD)]

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response

            from trajectoryrl.utils.pack_generator import generate_agents_md
            result = generate_agents_md(api_key="sk-test", model="claude-sonnet-4-5-20250929")

        assert len(result) > 0
        assert "Workplace Assistant Policy" in result
        # Verify the API was called with system prompt
        call_kwargs = MockClient.return_value.messages.create.call_args
        assert "system" in call_kwargs.kwargs
        assert call_kwargs.kwargs["messages"][0]["role"] == "user"

    def test_generate_improve(self):
        """Second call (with previous) uses improvement prompt."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=FAKE_AGENTS_MD_V2)]

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response

            from trajectoryrl.utils.pack_generator import generate_agents_md
            result = generate_agents_md(
                api_key="sk-test",
                previous_agents_md=FAKE_AGENTS_MD,
            )

        assert "Improved" in result
        # Verify the user message contains the previous policy
        call_kwargs = MockClient.return_value.messages.create.call_args
        user_msg = call_kwargs.kwargs["messages"][0]["content"]
        assert "current_policy" in user_msg

    def test_strips_code_fences(self):
        """Code fences around output are stripped."""
        wrapped = f"```markdown\n{FAKE_AGENTS_MD}\n```"
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=wrapped)]

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response

            from trajectoryrl.utils.pack_generator import generate_agents_md
            result = generate_agents_md(api_key="sk-test")

        assert not result.startswith("```")
        assert not result.endswith("```")

    def test_truncates_long_output(self):
        """Output over 28K chars is truncated."""
        long_content = "x" * 30_000
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=long_content)]

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response

            from trajectoryrl.utils.pack_generator import generate_agents_md
            result = generate_agents_md(api_key="sk-test")

        assert len(result) == 28_000

    def test_empty_response_raises(self):
        """Empty LLM response raises ValueError."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="   ")]

        with patch("anthropic.Anthropic") as MockClient:
            MockClient.return_value.messages.create.return_value = mock_response

            from trajectoryrl.utils.pack_generator import generate_agents_md
            with pytest.raises(ValueError, match="empty"):
                generate_agents_md(api_key="sk-test")


class TestS3Upload:
    """Tests for upload_pack_to_s3()."""

    def test_upload_returns_url(self):
        """Upload returns the correct public URL."""
        from trajectoryrl.base.miner import TrajectoryMiner
        pack = TrajectoryMiner.build_pack(agents_md="# Test")

        with patch("boto3.client") as mock_boto:
            from trajectoryrl.utils.s3_upload import upload_pack_to_s3
            url = upload_pack_to_s3(pack, bucket="my-bucket", key="pack.json", region="us-west-2")

        assert url == "https://my-bucket.s3.us-west-2.amazonaws.com/pack.json"

    def test_upload_sends_canonical_json(self):
        """Upload body matches compute_pack_hash serialization."""
        from trajectoryrl.base.miner import TrajectoryMiner
        pack = TrajectoryMiner.build_pack(agents_md="# Test")
        expected = json.dumps(pack, sort_keys=True).encode()

        with patch("boto3.client") as mock_boto:
            from trajectoryrl.utils.s3_upload import upload_pack_to_s3
            upload_pack_to_s3(pack, bucket="b", key="k")

        put_call = mock_boto.return_value.put_object
        put_call.assert_called_once()
        assert put_call.call_args.kwargs["Body"] == expected

    def test_upload_sets_content_type_and_acl(self):
        """Upload sets correct ContentType and ACL."""
        from trajectoryrl.base.miner import TrajectoryMiner
        pack = TrajectoryMiner.build_pack(agents_md="# Test")

        with patch("boto3.client") as mock_boto:
            from trajectoryrl.utils.s3_upload import upload_pack_to_s3
            upload_pack_to_s3(pack, bucket="b", key="k")

        kwargs = mock_boto.return_value.put_object.call_args.kwargs
        assert kwargs["ContentType"] == "application/json"
        assert kwargs["ACL"] == "public-read"


class TestRunDefaultFlow:
    """Integration test for the full _run_default loop."""

    @pytest.mark.asyncio
    async def test_full_cycle_with_s3(self):
        """Full cycle: generate → build → validate → upload → submit."""
        config = _make_config()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=FAKE_AGENTS_MD)]

        with (
            patch("anthropic.Anthropic") as MockAnthropic,
            patch("boto3.client") as mock_boto,
            patch("trajectoryrl.base.miner.TrajectoryMiner.submit_commitment", return_value=True),
            patch("trajectoryrl.base.miner.TrajectoryMiner.get_current_commitment", return_value=None),
        ):
            MockAnthropic.return_value.messages.create.return_value = mock_response

            from neurons.miner import _run_default

            # Run one iteration then cancel
            task = asyncio.create_task(_run_default(config))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Verify generate was called
            MockAnthropic.return_value.messages.create.assert_called()

            # Verify S3 upload was called
            mock_boto.return_value.put_object.assert_called()
            s3_kwargs = mock_boto.return_value.put_object.call_args.kwargs
            assert s3_kwargs["Bucket"] == "test-bucket"
            assert s3_kwargs["Key"] == "pack.json"

    @pytest.mark.asyncio
    async def test_full_cycle_with_pack_url(self):
        """With PACK_URL set, saves locally instead of S3 upload."""
        config = _make_config(
            s3_bucket="",
            pack_url="https://myserver.com/pack.json",
        )

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=FAKE_AGENTS_MD)]

        with (
            patch("anthropic.Anthropic") as MockAnthropic,
            patch("boto3.client") as mock_boto,
            patch("trajectoryrl.base.miner.TrajectoryMiner.submit_commitment", return_value=True),
            patch("trajectoryrl.base.miner.TrajectoryMiner.get_current_commitment", return_value=None),
            patch("trajectoryrl.base.miner.TrajectoryMiner.save_pack") as mock_save,
        ):
            MockAnthropic.return_value.messages.create.return_value = mock_response

            from neurons.miner import _run_default

            task = asyncio.create_task(_run_default(config))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # S3 should NOT be called
            mock_boto.return_value.put_object.assert_not_called()

            # save_pack should be called for local save
            mock_save.assert_called()

    @pytest.mark.asyncio
    async def test_skips_unchanged_hash(self):
        """If pack hash matches on-chain hash, skip submission."""
        expected_hash = _fake_pack_hash(FAKE_AGENTS_MD)
        config = _make_config()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=FAKE_AGENTS_MD)]

        mock_miner = MagicMock()
        mock_miner.submit = MagicMock(return_value=True)
        mock_miner.close = MagicMock()

        with (
            patch("anthropic.Anthropic") as MockAnthropic,
            patch("boto3.client"),
            patch("neurons.miner._make_miner", return_value=mock_miner),
            patch("neurons.miner._get_onchain_hash", return_value=expected_hash),
        ):
            MockAnthropic.return_value.messages.create.return_value = mock_response

            from neurons.miner import _run_default

            task = asyncio.create_task(_run_default(config))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # submit should NOT be called since hash matches
            mock_miner.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_validation_failure_skips_submission(self):
        """If pack validation fails, skip upload and submission."""
        # Generate content that will create a pack > 32KB (the size limit)
        huge_content = "x" * 28_000  # Will be within AGENTS.md char limit but pack might still pass
        config = _make_config()

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=huge_content)]

        with (
            patch("anthropic.Anthropic") as MockAnthropic,
            patch("boto3.client") as mock_boto,
            patch("trajectoryrl.base.miner.TrajectoryMiner.submit_commitment") as mock_submit,
            patch("trajectoryrl.base.miner.TrajectoryMiner.get_current_commitment", return_value=None),
            patch("trajectoryrl.base.miner.TrajectoryMiner.validate") as mock_validate,
        ):
            from trajectoryrl.utils.opp_schema import ValidationResult
            mock_validate.return_value = ValidationResult(passed=False, issues=["Pack too large"])
            MockAnthropic.return_value.messages.create.return_value = mock_response

            from neurons.miner import _run_default

            task = asyncio.create_task(_run_default(config))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Neither upload nor submit should be called
            mock_boto.return_value.put_object.assert_not_called()
            mock_submit.assert_not_called()


class TestConfigValidation:
    """Test fail-fast config checks in _run_default."""

    def test_missing_api_key_exits(self):
        config = _make_config(anthropic_api_key="")
        with pytest.raises(SystemExit):
            asyncio.run(_run_default_sync(config))

    def test_missing_s3_and_url_exits(self):
        config = _make_config(s3_bucket="", pack_url="")
        with pytest.raises(SystemExit):
            asyncio.run(_run_default_sync(config))

    def test_s3_bucket_sufficient(self):
        """S3_BUCKET alone is sufficient (no PACK_URL needed)."""
        config = _make_config(s3_bucket="my-bucket", pack_url="")
        # Should not exit — will proceed to the loop
        # We just test config validation passes by checking it reaches the miner init
        with (
            patch("neurons.miner._make_miner") as mock_miner,
            patch("neurons.miner._get_onchain_hash", return_value=None),
            patch("anthropic.Anthropic"),
            patch("boto3.client"),
        ):
            mock_miner.return_value.close = MagicMock()

            async def _run():
                from neurons.miner import _run_default
                task = asyncio.create_task(_run_default(config))
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(_run())
            mock_miner.assert_called_once()

    def test_pack_url_sufficient(self):
        """PACK_URL alone is sufficient (no S3_BUCKET needed)."""
        config = _make_config(s3_bucket="", pack_url="https://example.com/pack.json")
        with (
            patch("neurons.miner._make_miner") as mock_miner,
            patch("neurons.miner._get_onchain_hash", return_value=None),
            patch("anthropic.Anthropic"),
        ):
            mock_miner.return_value.close = MagicMock()

            async def _run():
                from neurons.miner import _run_default
                task = asyncio.create_task(_run_default(config))
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            asyncio.run(_run())
            mock_miner.assert_called_once()


async def _run_default_sync(config):
    """Helper to run _run_default and let it exit on config validation."""
    from neurons.miner import _run_default
    await _run_default(config)
