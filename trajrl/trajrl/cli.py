"""trajrl — CLI for the TrajectoryRL subnet."""

from __future__ import annotations

import json
import sys
from typing import Annotated, Optional

import typer

from trajrl.api import TrajRLClient
from trajrl import display as fmt

app = typer.Typer(
    name="trajrl",
    help="CLI for the TrajectoryRL subnet — query live validator, miner, and evaluation data.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

# -- shared option defaults ------------------------------------------------

_json_opt = typer.Option("--json", "-j", help="Force JSON output (auto when piped).")
_base_url_opt = typer.Option("--base-url", help="API base URL.", envvar="TRAJRL_BASE_URL")


def _client(base_url: str) -> TrajRLClient:
    return TrajRLClient(base_url=base_url)


def _want_json(flag: bool) -> bool:
    return flag or not sys.stdout.isatty()


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


# -- commands --------------------------------------------------------------

@app.command()
def status(
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Network health overview — validators, submissions, models."""
    client = _client(base_url)
    vali_data = client.validators()
    subs_data = client.submissions()
    if _want_json(json_output):
        _print_json({"validators": vali_data, "submissions": subs_data})
    else:
        fmt.display_status(vali_data, subs_data)


@app.command()
def validators(
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """List all validators with heartbeat status and LLM model."""
    data = _client(base_url).validators()
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_validators(data)


@app.command()
def scores(
    validator: Annotated[str, typer.Argument(help="Validator SS58 hotkey.")],
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Per-miner evaluation scores from a specific validator."""
    data = _client(base_url).scores_by_validator(validator)
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_scores(data)


@app.command()
def miner(
    hotkey: Annotated[str, typer.Argument(help="Miner SS58 hotkey.")],
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Detailed evaluation data for a specific miner."""
    data = _client(base_url).miner(hotkey)
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_miner(data)


@app.command()
def pack(
    hotkey: Annotated[str, typer.Argument(help="Miner SS58 hotkey.")],
    pack_hash: Annotated[str, typer.Argument(help="Pack SHA-256 hash.")],
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Evaluation data for a specific miner's pack."""
    data = _client(base_url).pack(hotkey, pack_hash)
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_pack(data)


@app.command()
def submissions(
    failed: Annotated[bool, typer.Option("--failed", help="Show only failed submissions.")] = False,
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Recent pack submissions (passed and failed)."""
    data = _client(base_url).submissions()
    if failed:
        data["submissions"] = [s for s in data.get("submissions", []) if s.get("evalStatus") == "failed"]
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_submissions(data, failed_only=failed)


@app.command()
def logs(
    validator: Annotated[Optional[str], typer.Option("--validator", "-v", help="Filter by validator hotkey.")] = None,
    miner_key: Annotated[Optional[str], typer.Option("--miner", "-m", help="Filter by miner hotkey.")] = None,
    log_type: Annotated[Optional[str], typer.Option("--type", "-t", help="Log type: 'miner' or 'cycle'.")] = None,
    eval_id: Annotated[Optional[str], typer.Option("--eval-id", help="Filter by eval cycle ID.")] = None,
    pack_hash: Annotated[Optional[str], typer.Option("--pack-hash", help="Filter by pack hash.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", help="Max results to return.")] = 50,
    json_output: Annotated[bool, _json_opt] = False,
    base_url: Annotated[str, _base_url_opt] = "https://trajrl.com",
) -> None:
    """Evaluation log archives uploaded by validators."""
    data = _client(base_url).eval_logs(
        validator=validator,
        miner=miner_key,
        log_type=log_type,
        eval_id=eval_id,
        pack_hash=pack_hash,
        limit=limit,
    )
    if _want_json(json_output):
        _print_json(data)
    else:
        fmt.display_logs(data)
