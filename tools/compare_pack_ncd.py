#!/usr/bin/env python3
"""Compare two packs reachable via pack_url using validator NCD logic.

Fetches pack.json from each HTTP(S) URL and runs the same AGENTS.md comparison
as trajectoryrl.utils.ncd (used by trajectoryrl.base.validator deduplication).

Usage:
    python scripts/compare_pack_ncd.py <pack_url_a> <pack_url_b>
    python scripts/compare_pack_ncd.py <pack_url_a> <pack_url_b> --threshold 0.85
    python scripts/compare_pack_ncd.py <pack_url_a> <pack_url_b> -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import zlib
from pathlib import Path
from typing import Any

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trajectoryrl.utils.ncd import (  # noqa: E402
    SIMILARITY_THRESHOLD,
    normalize_policy,
    pack_similarity,
)

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
_MAX_RESPONSE_BYTES = 64 * 1024


def _fetch_pack_json(url: str) -> dict[str, Any]:
    with httpx.Client(follow_redirects=True, timeout=_HTTP_TIMEOUT) as client:
        resp = client.get(
            url,
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {url}")
    raw = resp.content
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise RuntimeError(
            f"Response too large ({len(raw)} bytes, max {_MAX_RESPONSE_BYTES}) from {url}"
        )
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON at {url}: {e}") from e


def _require_agents_md(pack: dict[str, Any], label: str) -> None:
    try:
        text = pack["files"]["AGENTS.md"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"{label}: missing pack['files']['AGENTS.md']") from e
    if not isinstance(text, str):
        raise RuntimeError(f"{label}: AGENTS.md must be a string")


def _ncd_verbose(pack_a: dict[str, Any], pack_b: dict[str, Any]) -> None:
    a = normalize_policy(pack_a["files"]["AGENTS.md"]).encode("utf-8")
    b = normalize_policy(pack_b["files"]["AGENTS.md"]).encode("utf-8")
    ca = len(zlib.compress(a, 9))
    cb = len(zlib.compress(b, 9))
    cab = len(zlib.compress(a + b, 9))
    max_c = max(ca, cb)
    if max_c == 0:
        ncd = 0.0
        similarity = 1.0
    else:
        ncd = (cab - min(ca, cb)) / max_c
        similarity = 1.0 - ncd
    print("Verbose (normalized AGENTS.md, zlib level 9):")
    print(f"  len(compress(A)) = {ca}")
    print(f"  len(compress(B)) = {cb}")
    print(f"  len(compress(A+B)) = {cab}")
    print(f"  NCD = (C_AB - min(C_A,C_B)) / max(C_A,C_B) = {ncd:.6f}")
    print(f"  similarity = 1 - NCD = {similarity:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NCD similarity between two packs (AGENTS.md), same as validator NCD layer."
    )
    parser.add_argument("pack_url_a", help="HTTP(S) URL to first pack.json")
    parser.add_argument("pack_url_b", help="HTTP(S) URL to second pack.json")
    parser.add_argument(
        "--threshold",
        type=float,
        default=SIMILARITY_THRESHOLD,
        help=f"Flag as 'too similar' if similarity >= this (default: {SIMILARITY_THRESHOLD})",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print zlib sizes and raw NCD formula terms",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print similarity number (or errors on stderr)",
    )
    parser.add_argument(
        "--fail-on-similar",
        action="store_true",
        help="Exit with code 1 if similarity >= threshold (after a successful fetch)",
    )
    args = parser.parse_args()

    if not args.quiet:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        logger.info("Fetching A: %s", args.pack_url_a)
        pack_a = _fetch_pack_json(args.pack_url_a)
        _require_agents_md(pack_a, "Pack A")

        logger.info("Fetching B: %s", args.pack_url_b)
        pack_b = _fetch_pack_json(args.pack_url_b)
        _require_agents_md(pack_b, "Pack B")

        similarity = pack_similarity(pack_a, pack_b)
        too_similar = similarity >= args.threshold

        if not args.quiet:
            print(f"pack_url A: {args.pack_url_a}")
            print(f"pack_url B: {args.pack_url_b}")
            print(f"NCD similarity (AGENTS.md): {similarity:.6f}")
            print(f"Threshold: {args.threshold:.6f}")
            print(
                "Would dedup as copy (similarity >= threshold): "
                f"{'yes' if too_similar else 'no'}"
            )
            if args.verbose:
                print()
                _ncd_verbose(pack_a, pack_b)
        else:
            print(f"{similarity:.6f}")

        if args.fail_on_similar and too_similar:
            return 1
        return 0
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
