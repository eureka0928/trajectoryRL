"""Typed HTTP client for the TrajectoryRL public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://trajrl.com"
_TIMEOUT = 30.0


@dataclass
class TrajRLClient:
    base_url: str = DEFAULT_BASE_URL
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    # -- endpoints ---------------------------------------------------------

    def validators(self) -> dict[str, Any]:
        """GET /api/validators"""
        return self._get("/api/validators")

    def scores_by_validator(self, validator: str) -> dict[str, Any]:
        """GET /api/scores/by-validator?validator=<hotkey>"""
        return self._get("/api/scores/by-validator", params={"validator": validator})

    def miner(self, hotkey: str) -> dict[str, Any]:
        """GET /api/miners/:hotkey"""
        return self._get(f"/api/miners/{hotkey}")

    def pack(self, hotkey: str, pack_hash: str) -> dict[str, Any]:
        """GET /api/miners/:hotkey/packs/:packHash"""
        return self._get(f"/api/miners/{hotkey}/packs/{pack_hash}")

    def submissions(self, limit: int | None = None) -> dict[str, Any]:
        """GET /api/submissions"""
        return self._get("/api/submissions", params=_compact({"limit": limit}))

    def eval_logs(
        self,
        *,
        validator: str | None = None,
        miner: str | None = None,
        log_type: str | None = None,
        eval_id: str | None = None,
        pack_hash: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        """GET /api/eval-logs"""
        params = _compact({
            "validator": validator,
            "miner": miner,
            "type": log_type,
            "eval_id": eval_id,
            "pack_hash": pack_hash,
            "from": from_date,
            "to": to_date,
            "limit": limit,
            "offset": offset,
        })
        return self._get("/api/eval-logs", params=params)

    # -- internal ----------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()


def _compact(d: dict) -> dict:
    """Remove None values from a dict."""
    return {k: v for k, v in d.items() if v is not None}
