"""Dual-backend CAS for consensus payloads: IPFS primary, trajrl.com/GCS fallback.

Upload: try IPFS first, then trajrl.com API (which stores to GCS) if IPFS fails.
Download: try IPFS kubo API first, then public gateways, then GCS URL fallback.
Both backends are written on upload (best-effort) for redundancy.

Pointer registry is on-chain via Bittensor ``set_commitment`` — not handled here.
"""

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional

import aiohttp

from .consensus import (
    ConsensusPayload,
    verify_payload_integrity,
)

logger = logging.getLogger(__name__)

# Maximum payload size (10 MB).  Consensus payloads are small JSON documents;
# anything larger is either corrupt or a denial-of-service attempt.
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024


class CASBackend(ABC):
    """Abstract content-addressed storage backend."""

    @abstractmethod
    async def upload(self, data: bytes) -> Optional[str]:
        """Upload data, return content address or None on failure."""
        ...

    @abstractmethod
    async def download(self, address: str) -> Optional[bytes]:
        """Download data by content address, return bytes or None."""
        ...


class IPFSBackend(CASBackend):
    """IPFS via kubo-compatible HTTP API with public gateway fallback.

    Upload:   POST {api_url}/add
    Download: POST {api_url}/cat?arg={cid} (primary),
              then GET {gateway}/ipfs/{cid} for each fallback gateway.

    api_url should include the /api/v0 prefix,
    e.g. ``http://ipfs.metahash73.com:5001/api/v0``.
    """

    def __init__(
        self,
        api_url: str = "http://ipfs.metahash73.com:5001/api/v0",
        gateway_urls: Optional[List[str]] = None,
    ):
        self.api_url = api_url.rstrip("/")
        self._gateway_urls = [
            gw.rstrip("/") for gw in (gateway_urls or [])
        ]

    async def upload(self, data: bytes) -> Optional[str]:
        try:
            url = f"{self.api_url}/add"
            form = aiohttp.FormData()
            form.add_field("file", data, content_type="application/octet-stream")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.warning("IPFS upload failed: HTTP %d", resp.status)
                        return None
                    result = await resp.json()
                    cid = result.get("Hash")
                    if cid:
                        logger.info("IPFS upload OK: CID=%s", cid)
                    return cid
        except Exception as e:
            logger.warning("IPFS upload error: %s", e)
            return None

    async def download(self, address: str) -> Optional[bytes]:
        """Download CID: try kubo API first, then public gateways sequentially."""
        data = await self._download_via_api(address)
        if data is not None:
            return data
        for gw in self._gateway_urls:
            data = await self._download_via_gateway(gw, address)
            if data is not None:
                return data
        return None

    async def _download_via_api(self, cid: str) -> Optional[bytes]:
        """Download via kubo HTTP API (POST /cat)."""
        try:
            url = f"{self.api_url}/cat"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    params={"arg": cid},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("IPFS API download failed: HTTP %d (CID=%s)", resp.status, cid)
                        return None
                    return await self._read_body(resp, cid, "IPFS API")
        except Exception as e:
            logger.warning("IPFS API download error (CID=%s): %s", cid, e)
            return None

    async def _download_via_gateway(self, gateway_url: str, cid: str) -> Optional[bytes]:
        """Download via public IPFS gateway (HTTP GET)."""
        url = f"{gateway_url}/ipfs/{cid}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "IPFS gateway %s download failed: HTTP %d (CID=%s)",
                            gateway_url, resp.status, cid,
                        )
                        return None
                    return await self._read_body(resp, cid, gateway_url)
        except Exception as e:
            logger.warning("IPFS gateway %s download error (CID=%s): %s", gateway_url, cid, e)
            return None

    @staticmethod
    async def _read_body(resp: aiohttp.ClientResponse, cid: str, source: str) -> Optional[bytes]:
        if resp.content_length and resp.content_length > MAX_PAYLOAD_BYTES:
            logger.warning("%s download too large: %d bytes (CID=%s)", source, resp.content_length, cid)
            return None
        data = await resp.content.read(MAX_PAYLOAD_BYTES + 1)
        if len(data) > MAX_PAYLOAD_BYTES:
            logger.warning("%s download exceeded max size: %d bytes (CID=%s)", source, len(data), cid)
            return None
        if not data:
            logger.warning("%s download returned empty body (CID=%s)", source, cid)
            return None
        # Streaming responses may be truncated (connection closed mid-transfer)
        # while still returning HTTP 200.  Validate JSON completeness so that
        # truncated data is discarded and the caller can fall through to the
        # next source instead of treating corrupt bytes as a successful download.
        try:
            json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(
                "%s download returned truncated/corrupt JSON (%d bytes, CID=%s): %s",
                source, len(data), cid, e,
            )
            return None
        logger.debug("%s download OK: CID=%s, %d bytes", source, cid, len(data))
        return data


class TrajRLAPIBackend(CASBackend):
    """GCS proxy via trajrl.com API.

    Upload:   POST /api/v1/consensus/payload → stores to GCS, returns public URL
    Download: GET {url} — direct download from the public GCS URL
    """

    def __init__(
        self,
        base_url: str = "https://trajrl.com",
        sign_fn=None,
        validator_hotkey: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self._sign_fn = sign_fn
        self._validator_hotkey = validator_hotkey

    async def upload(self, data: bytes) -> Optional[str]:
        """Upload payload via API. Returns a public GCS URL."""
        try:
            import time
            ts = int(time.time())
            payload_dict = json.loads(data.decode("utf-8"))

            body = {
                "validator_hotkey": self._validator_hotkey,
                "timestamp": ts,
                "signature": "",
                "payload": payload_dict,
            }
            if self._sign_fn:
                msg = f"trajectoryrl-consensus:{self._validator_hotkey}:{ts}"
                body["signature"] = self._sign_fn(msg)

            url = f"{self.base_url}/api/v1/consensus/payload"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status in (200, 409):
                        result = await resp.json()
                        public_url = result.get("url")
                        if public_url:
                            logger.info("API upload OK: url=%s", public_url)
                            return public_url
                        content_hash = result.get("content_hash", "")
                        logger.info("API upload OK: hash=%s", content_hash)
                        return content_hash
                    logger.warning("API upload failed: HTTP %d", resp.status)
                    return None
        except Exception as e:
            logger.warning("API upload error: %s", e)
            return None

    async def download(self, address: str) -> Optional[bytes]:
        """Download payload by direct URL (GCS or other public URL)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    address, timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("URL download failed: HTTP %d (url=%s)", resp.status, address)
                        return None
                    if resp.content_length and resp.content_length > MAX_PAYLOAD_BYTES:
                        logger.warning("URL download too large: %d bytes (url=%s)", resp.content_length, address[:60])
                        return None
                    data = await resp.content.read(MAX_PAYLOAD_BYTES + 1)
                    if len(data) > MAX_PAYLOAD_BYTES:
                        logger.warning("URL download exceeded max size: %d bytes (url=%s)", len(data), address[:60])
                        return None
                    if not data:
                        logger.warning("URL download returned empty body (url=%s)", address[:60])
                        return None
                    try:
                        json.loads(data)
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.warning(
                            "URL download returned truncated/corrupt JSON (%d bytes, url=%s): %s",
                            len(data), address[:60], e,
                        )
                        return None
                    logger.debug("URL download OK: url=%s, %d bytes", address[:60], len(data))
                    return data
        except Exception as e:
            logger.warning("URL download error (url=%s): %s", address[:60], e)
            return None


class ConsensusStore:
    """Dual-backend CAS with IPFS primary, trajrl.com/GCS fallback.

    Pointer registry is handled on-chain (not here).
    """

    def __init__(self, ipfs: IPFSBackend, api: TrajRLAPIBackend):
        self.ipfs = ipfs
        self.api = api

    async def upload_payload(self, payload: ConsensusPayload) -> Optional[str]:
        """Upload payload to CAS. Returns content address (GCS URL or IPFS CID).

        Strategy: upload to both backends concurrently for redundancy.
        Prefer GCS URL as the returned address (universally accessible
        without a local IPFS node). Fall back to IPFS CID if API fails.
        """
        data = payload.serialize()

        ipfs_result, api_result = await asyncio.gather(
            self.ipfs.upload(data),
            self.api.upload(data),
            return_exceptions=True,
        )

        if isinstance(ipfs_result, Exception):
            logger.warning("IPFS upload raised: %s", ipfs_result)
            ipfs_result = None
        if isinstance(api_result, Exception):
            logger.warning("API upload raised: %s", api_result)
            api_result = None

        if ipfs_result:
            return ipfs_result
        if api_result:
            return api_result

        logger.error("Both IPFS and API upload failed for window %d", payload.window_number)
        return None

    async def download_payload(self, content_address: str) -> Optional[ConsensusPayload]:
        """Download and verify payload from CAS.

        Determines backend by address format:
        - IPFS CID (Qm... / bafy...): download via IPFS
        - HTTP(S) URL: download directly (GCS or other public URL)
        """
        data = None

        is_url = content_address.startswith("http://") or content_address.startswith("https://")
        is_ipfs_cid = not is_url

        if is_ipfs_cid:
            data = await self.ipfs.download(content_address)

        if data is None and is_url:
            data = await self.api.download(content_address)

        if data is None:
            logger.warning("Failed to download payload: %s", content_address[:60])
            return None

        # Verify integrity against the pointer's content address (not self-reported hash).
        # For sha256-addressed payloads, check the raw bytes match the pointer.
        # IPFS CIDs are verified by IPFS itself; skip hash check for those.
        if content_address.startswith("sha256:"):
            if not verify_payload_integrity(data, content_address):
                logger.warning(
                    "Payload integrity check failed: pointer=%s, computed=%s",
                    content_address[:60],
                    "sha256:" + hashlib.sha256(data).hexdigest()[:16],
                )
                return None

        try:
            payload = ConsensusPayload.deserialize(data)
        except Exception as e:
            logger.warning("Failed to deserialize payload %s: %s", content_address[:60], e)
            return None

        return payload
