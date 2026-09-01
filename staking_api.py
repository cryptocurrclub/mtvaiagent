"""MultiVAC staking data, sourced from the official explorer's backend API.

MultiVAC's native-chain "staking" is not a smart contract: per the staking
portal's own client code (https://e.mtv.ac/staking.html -> js/staking.js),
staking on the native chain is a plain native-coin transfer to a fixed
system address, and staked balances/leaderboard rank are tracked off-chain
by the explorer backend and served over its REST API - not readable via
eth_call. This module talks to that same REST API. Endpoints and the APR
formula were reverse-engineered from the portal's obfuscated JS bundle, are
undocumented, and may change without notice.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests

import config
from blockchain import validate_address
from cache import DiskCache
from retry import call_with_retry

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (requests.ConnectionError, requests.Timeout, requests.HTTPError)

DECIMALS = config.NATIVE_DECIMALS


class StakingAPIError(Exception):
    """Raised when the staking backend can't be reached or returns something unexpected."""


@dataclass
class StakingSummary:
    address: str
    rank: Optional[int]
    mainnet_staked: Decimal
    erc20_staked: Decimal
    bep20_staked: Decimal
    total_staked: Decimal
    withdraw_pending: Decimal
    withdraw_success: Decimal
    apr_percent: Optional[float]
    source: str
    fetched_at: float
    from_cache: bool = False


def _wei_to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value)) / (Decimal(10) ** DECIMALS)
    except (InvalidOperation, TypeError):
        return Decimal(0)


class StakingApiClient:
    """Fetches per-address staking data from MultiVAC's explorer backend."""

    def __init__(
        self,
        base_url: str = config.STAKING_API_BASE_URL,
        timeout: float = config.REQUEST_TIMEOUT_SECONDS,
        max_retries: int = config.MAX_RETRIES,
        backoff_base: float = config.RETRY_BACKOFF_BASE_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.cache = DiskCache(config.CACHE_DIR)

    def _post(self, path: str, params: dict) -> dict | int | float:
        def _do_call():
            resp = requests.post(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()

        return call_with_retry(
            _do_call,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            retry_on=RETRYABLE_EXCEPTIONS,
            what=f"staking API {path}",
        )

    def get_staking_summary(self, address: str) -> StakingSummary:
        checksummed = validate_address(address)
        cache_key = f"staking_{checksummed}"
        cached = self.cache.get(cache_key, config.BALANCE_CACHE_TTL_SECONDS)
        if cached:
            return StakingSummary(
                address=checksummed,
                rank=cached["rank"],
                mainnet_staked=Decimal(cached["mainnet_staked"]),
                erc20_staked=Decimal(cached["erc20_staked"]),
                bep20_staked=Decimal(cached["bep20_staked"]),
                total_staked=Decimal(cached["total_staked"]),
                withdraw_pending=Decimal(cached["withdraw_pending"]),
                withdraw_success=Decimal(cached["withdraw_success"]),
                apr_percent=cached["apr_percent"],
                source=cached["source"],
                fetched_at=cached["_cached_at"],
                from_cache=True,
            )

        try:
            data = self._post("/stake/getByAddress", {"address": checksummed})
        except Exception as exc:
            raise StakingAPIError(f"Could not reach MultiVAC staking API: {exc}") from exc

        if not isinstance(data, dict) or "totals" not in data:
            raise StakingAPIError(f"Unexpected staking API response: {data}")

        apr_percent: Optional[float] = None
        try:
            count = self._post("/stake/count", {})
            count = float(count)
            if count > 0:
                apr_percent = config.STAKING_APR_NUMERATOR / count
        except Exception as exc:
            logger.warning("Could not compute staking APR (non-fatal): %s", exc)

        summary = StakingSummary(
            address=checksummed,
            rank=data.get("rank"),
            mainnet_staked=_wei_to_decimal(data.get("mainnet", 0)),
            erc20_staked=_wei_to_decimal(data.get("erc20", 0)),
            bep20_staked=_wei_to_decimal(data.get("bep20", 0)),
            total_staked=_wei_to_decimal(data.get("totals", 0)),
            withdraw_pending=_wei_to_decimal(data.get("withdrawPending", 0)),
            withdraw_success=_wei_to_decimal(data.get("withdrawSuccess", 0)),
            apr_percent=apr_percent,
            source="e.mtv.ac staking API",
            fetched_at=time.time(),
        )

        self.cache.set(
            cache_key,
            {
                "rank": summary.rank,
                "mainnet_staked": str(summary.mainnet_staked),
                "erc20_staked": str(summary.erc20_staked),
                "bep20_staked": str(summary.bep20_staked),
                "total_staked": str(summary.total_staked),
                "withdraw_pending": str(summary.withdraw_pending),
                "withdraw_success": str(summary.withdraw_success),
                "apr_percent": summary.apr_percent,
                "source": summary.source,
            },
        )
        return summary
