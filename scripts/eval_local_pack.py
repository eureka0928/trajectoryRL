#!/usr/bin/env python3
"""Evaluate a local pack.json like the production validator.

Simulates the full validator flow:
  1. Loads pack.json from disk (no chain access needed)
  2. Validates OPP schema
  3. Runs all 5 scenarios with consensus voting (3 runs each)
  4. Computes weighted cost (same weights as production)
  5. Reports qualification gate (all safety + correctness checks must pass)

Usage:
    # Basic eval (services must be running: docker compose up -d in clawbench/):
    python scripts/eval_local_pack.py

    # Custom pack path:
    python scripts/eval_local_pack.py --pack ./pack.json

    # Single scenario:
    python scripts/eval_local_pack.py --scenarios client_escalation

    # Multiple consensus runs:
    python scripts/eval_local_pack.py --num-runs 3

    # Save results:
    python scripts/eval_local_pack.py -o results.json -v
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

# Add project root + clawbench to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "clawbench"))

from clawbench.scoring import score_episode, check_qualification_gate
from clawbench.runner import (
    DEFAULT_OPENCLAW_URL, DEFAULT_OPENCLAW_TOKEN, DEFAULT_MOCK_TOOLS_URL, DEFAULT_MODEL,
    wait_for_services, send_message, get_tool_calls, get_all_requests,
    reset_scenario, load_scenario, extract_usage, get_session_usage,
)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / "clawbench" / ".env")
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CLAWBENCH_DIR = PROJECT_ROOT / "clawbench"
SCENARIOS_DIR = CLAWBENCH_DIR / "scenarios"
FIXTURES_DIR = CLAWBENCH_DIR / "fixtures"
WORKSPACE_DIR = CLAWBENCH_DIR / "workspace"

OPENCLAW_URL = os.getenv("OPENCLAW_URL", DEFAULT_OPENCLAW_URL)
OPENCLAW_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", DEFAULT_OPENCLAW_TOKEN)
MOCK_TOOLS_URL = os.getenv("MOCK_TOOLS_URL", DEFAULT_MOCK_TOOLS_URL)
CLAWBENCH_MODEL = os.getenv("CLAWBENCH_DEFAULT_MODEL", DEFAULT_MODEL)

SCENARIO_WEIGHTS = {
    "client_escalation": 1.5,
    "inbox_to_action": 1.5,
    "morning_brief": 1.0,
    "team_standup": 1.0,
    "inbox_triage": 1.0,
}

DEFAULT_SCENARIOS = list(SCENARIO_WEIGHTS.keys())


def setup_workspace_for_scenario(scenario_name: str, agents_md: str):
    """Copy fixture files + our AGENTS.md into workspace."""
    fixture_dir = FIXTURES_DIR / scenario_name

    # Copy USER.md from fixtures (with template vars filled in)
    user_src = fixture_dir / "USER.md"
    if user_src.exists():
        user_md = user_src.read_text()
        # Default context (validator uses random epoch context, we use fixed)
        ctx = {
            "USER_NAME": "Alex Chen",
            "USER_FIRST_NAME": "Alex",
            "USER_ROLE": "Senior Product Manager",
            "COMPANY": "TechCorp",
        }
        for k, v in ctx.items():
            user_md = user_md.replace("{{" + k + "}}", v)
        (WORKSPACE_DIR / "USER.md").write_text(user_md)

    # Write our AGENTS.md
    (WORKSPACE_DIR / "AGENTS.md").write_text(agents_md)


def run_episode(scenario_name: str, agents_md: str, run_idx: int = 0) -> dict:
    """Run a single episode for one scenario."""
    # Load scenario config
    scenario = load_scenario(scenario_name, SCENARIOS_DIR)
    prompt = scenario.get("prompt", "").strip()

    # Setup workspace
    setup_workspace_for_scenario(scenario_name, agents_md)

    # Reset mock server
    reset_scenario(MOCK_TOOLS_URL, scenario_name)
    time.sleep(0.5)

    # Run episode
    session_key = f"eval-{scenario_name}-{run_idx}-{int(time.time() * 1000)}"
    t0 = time.time()
    raw_response = send_message(
        OPENCLAW_URL, OPENCLAW_TOKEN, prompt,
        model=CLAWBENCH_MODEL, session_key=session_key,
    )
    elapsed = time.time() - t0

    # Collect results
    tool_calls = get_tool_calls(MOCK_TOOLS_URL)
    all_reqs = get_all_requests(MOCK_TOOLS_URL)
    summary = all_reqs.get("summary", {})

    assistant_message = ""
    if "choices" in raw_response:
        assistant_message = raw_response["choices"][0].get("message", {}).get("content", "")

    # Extract usage/cost
    usage = extract_usage(raw_response)
    if not usage or usage.get("total_cost_usd") is None:
        session_usage = get_session_usage(OPENCLAW_URL, OPENCLAW_TOKEN, session_key)
        if session_usage:
            usage = session_usage

    result = {
        "scenario": scenario_name,
        "run_idx": run_idx,
        "response": assistant_message,
        "response_length": len(assistant_message),
        "tool_calls_total": len(tool_calls),
        "tool_calls_raw": tool_calls,
        "requests_total": summary.get("total", 0),
        "requests_success": summary.get("success", 0),
        "requests_failed": summary.get("failed", 0),
        "elapsed_seconds": round(elapsed, 1),
        "usage": usage,
        "raw_response": raw_response,
    }

    # Score
    scoring_config = scenario.get("scoring")
    if scoring_config:
        score = score_episode(result, scoring_config)
        result["score"] = score
        qualified, failed_gate = check_qualification_gate(score)
        result["qualified"] = qualified
        result["failed_gate_checks"] = failed_gate
    else:
        result["score"] = {"score": None}
        result["qualified"] = False
        result["failed_gate_checks"] = []

    return result


def majority_vote_results(runs: list) -> dict:
    """Majority-vote across multiple runs (like production consensus)."""
    if len(runs) == 1:
        return runs[0]

    # Take median cost (outlier-resistant)
    costs = [r["usage"].get("total_cost_usd", 0) for r in runs if r.get("usage")]
    costs = [c for c in costs if c and c > 0]
    median_cost = sorted(costs)[len(costs) // 2] if costs else 0

    # Majority vote qualification
    qualified_votes = sum(1 for r in runs if r.get("qualified"))
    quorum = (len(runs) // 2) + 1
    voted_qualified = qualified_votes >= quorum

    # Use the best run's data for reporting
    best_run = max(runs, key=lambda r: (r.get("qualified", False), r.get("score", {}).get("score", 0)))

    voted = {
        **best_run,
        "num_runs": len(runs),
        "qualified_votes": f"{qualified_votes}/{len(runs)}",
        "voted_qualified": voted_qualified,
        "median_cost_usd": median_cost,
        "all_costs": costs,
    }
    return voted


def main():
    parser = argparse.ArgumentParser(description="Evaluate local pack like production validator")
    parser.add_argument("--pack", type=str, default="pack.json", help="Path to pack.json")
    parser.add_argument("--agents-md", type=str, default=None, help="Path to AGENTS.md (overrides pack)")
    parser.add_argument("--scenarios", nargs="+", default=None, help="Scenarios to evaluate")
    parser.add_argument("--num-runs", type=int, default=1, help="Consensus runs per scenario (prod=3)")
    parser.add_argument("--output", "-o", type=str, default=None, help="Save results to JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed rubric results")
    parser.add_argument("--wait", "-w", action="store_true", help="Wait for services to be ready")

    args = parser.parse_args()

    # Load AGENTS.md
    if args.agents_md:
        agents_md = Path(args.agents_md).read_text()
        print(f"Using AGENTS.md: {args.agents_md} ({len(agents_md)} chars)")
    else:
        pack_path = Path(args.pack)
        if not pack_path.exists():
            print(f"ERROR: Pack not found: {pack_path}")
            sys.exit(1)
        with open(pack_path) as f:
            pack = json.load(f)
        agents_md = pack.get("files", {}).get("AGENTS.md", "")
        if not agents_md:
            print("ERROR: No AGENTS.md in pack")
            sys.exit(1)
        print(f"Using pack: {pack_path} ({len(agents_md)} chars AGENTS.md)")

    # Wait for services
    if args.wait:
        print("Waiting for services...")
        if not wait_for_services(MOCK_TOOLS_URL, OPENCLAW_URL, timeout=60):
            print("ERROR: Services not ready. Run: cd clawbench && docker compose up -d")
            sys.exit(1)

    scenarios = args.scenarios or DEFAULT_SCENARIOS
    print(f"Scenarios: {', '.join(scenarios)}")
    print(f"Consensus runs: {args.num_runs}")
    print(f"Model: {CLAWBENCH_MODEL}")
    print()

    # Run evaluations
    results = {}
    total_cost = 0.0

    for scenario_name in scenarios:
        print(f"{'=' * 60}")
        print(f"  {scenario_name} ({args.num_runs} run{'s' if args.num_runs > 1 else ''})")
        print(f"{'=' * 60}")

        runs = []
        for i in range(args.num_runs):
            if args.num_runs > 1:
                print(f"  Run {i + 1}/{args.num_runs}...")
            result = run_episode(scenario_name, agents_md, run_idx=i)
            runs.append(result)

            # Quick status
            score = result.get("score", {})
            checks = score.get("checks", [])
            passed = sum(1 for c in checks if c.get("passed"))
            total = len(checks)
            gate = "PASS" if result.get("qualified") else "FAIL"
            cost_usd = result.get("usage", {}).get("total_cost_usd", 0) or 0
            print(f"  Checks: {passed}/{total}  {gate}  "
                  f"Tools: {result['tool_calls_total']}  "
                  f"Cost: ${cost_usd:.4f}  "
                  f"Resp: {result['response_length']} chars")

            if args.verbose and result.get("failed_gate_checks"):
                for fc in result["failed_gate_checks"]:
                    if isinstance(fc, dict):
                        print(f"    FAIL  {fc['id']}: {fc.get('description', '')}")
                    else:
                        print(f"    FAIL  {fc}")

        # Consensus vote
        voted = majority_vote_results(runs)
        results[scenario_name] = voted

        cost = voted.get("median_cost_usd") or voted.get("usage", {}).get("total_cost_usd", 0) or 0
        total_cost += cost

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  EVALUATION SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Scenario':<25} {'Checks':>8} {'Gate':>6} {'Calls':>6} {'Cost':>10}")
    print(f"{'-' * 70}")

    all_qualified = True
    weighted_cost = 0.0
    total_weight = 0.0

    for scenario_name in scenarios:
        r = results[scenario_name]
        score = r.get("score", {})
        checks = score.get("checks", [])
        passed = sum(1 for c in checks if c.get("passed"))
        total_checks = len(checks)

        qualified = r.get("voted_qualified", r.get("qualified", False))
        gate = "PASS" if qualified else "FAIL"
        if not qualified:
            all_qualified = False

        cost = r.get("median_cost_usd") or r.get("usage", {}).get("total_cost_usd", 0) or 0
        calls = r.get("tool_calls_total", 0)

        w = SCENARIO_WEIGHTS.get(scenario_name, 1.0)
        weighted_cost += w * cost
        total_weight += w

        print(f"{scenario_name:<25} {passed:>3}/{total_checks:<4} {gate:>6} {calls:>6} ${cost:>9.4f}")

    avg_weighted_cost = weighted_cost / total_weight if total_weight > 0 else 0
    print(f"{'-' * 70}")
    print(f"{'TOTAL (sum)':<25} {'':>8} {'':>6} {'':>6} ${total_cost:>9.4f}")
    print(f"{'WEIGHTED AVG':<25} {'':>8} {'':>6} {'':>6} ${avg_weighted_cost:>9.4f}")
    print(f"{'=' * 70}")
    print(f"\nQualification: {'FULLY QUALIFIED' if all_qualified else 'NOT QUALIFIED'}")
    print(f"Weighted cost (production metric): ${avg_weighted_cost:.4f}")

    # Save results
    if args.output:
        output_data = {
            "agents_md_chars": len(agents_md),
            "model": CLAWBENCH_MODEL,
            "num_runs": args.num_runs,
            "scenarios": {},
            "summary": {
                "fully_qualified": all_qualified,
                "total_cost_usd": total_cost,
                "weighted_avg_cost": avg_weighted_cost,
            },
        }
        for name, r in results.items():
            score = r.get("score", {})
            checks = score.get("checks", [])
            output_data["scenarios"][name] = {
                "qualified": r.get("voted_qualified", r.get("qualified")),
                "checks_passed": sum(1 for c in checks if c.get("passed")),
                "checks_total": len(checks),
                "tool_calls": r.get("tool_calls_total"),
                "cost_usd": r.get("median_cost_usd") or r.get("usage", {}).get("total_cost_usd"),
                "response_length": r.get("response_length"),
                "response": r.get("response", ""),
                "failed_checks": [
                    {"id": c["id"], "desc": c.get("description", "")}
                    for c in checks if not c.get("passed")
                ],
            }
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
