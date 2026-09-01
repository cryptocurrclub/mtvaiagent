"""Blockchain connection layer for the MultiVAC (MTV) chain.

Wraps web3.py with connection verification, retries, and address
validation. MultiVAC's native-chain staking is not a smart contract (see
staking_api.py), so this module only deals with the native coin balance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from web3 import Web3
from web3.exceptions import Web3Exception

import config
from retry import call_with_retry

logger = logging.getLogger(__name__)


class MTVConnectionError(Exception):
    """Raised when the RPC endpoint cannot be reached or is unhealthy."""


class InvalidAddressError(ValueError):
    """Raised when a wallet address fails EIP-55/format validation."""


@dataclass
class NativeBalance:
    address: str
    raw_wei: int
    balance: Decimal
    symbol: str


def validate_address(address: str) -> str:
    """Return a checksummed address, or raise InvalidAddressError."""
    if not address or not isinstance(address, str):
        raise InvalidAddressError("Address must be a non-empty string")
    if not Web3.is_address(address):
        raise InvalidAddressError(f"'{address}' is not a valid EVM-style address")
    return Web3.to_checksum_address(address)


class MultiVacClient:
    """Thin, defensive wrapper around a Web3 connection to rpc.mtv.ac."""

    def __init__(
        self,
        rpc_url: str = config.RPC_URL,
        timeout: float = config.REQUEST_TIMEOUT_SECONDS,
        max_retries: int = config.MAX_RETRIES,
        backoff_base: float = config.RETRY_BACKOFF_BASE_SECONDS,
    ) -> None:
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._w3: Optional[Web3] = None

    def connect(self) -> Web3:
        """Establish (or reuse) a Web3 connection and verify it is live."""
        if self._w3 is not None and self.is_connected():
            return self._w3

        def _do_connect() -> Web3:
            w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": self.timeout}))
            if not w3.is_connected():
                raise MTVConnectionError(f"Could not reach RPC endpoint at {self.rpc_url}")
            # Round-trip a real call so a connection that "is_connected" but
            # returns garbage still gets caught before we trust it.
            _ = w3.eth.chain_id
            return w3

        try:
            self._w3 = call_with_retry(
                _do_connect,
                max_retries=self.max_retries,
                backoff_base=self.backoff_base,
                retry_on=(MTVConnectionError, Web3Exception, ConnectionError, TimeoutError, OSError),
                what=f"connect to {self.rpc_url}",
            )
        except Exception as exc:
            logger.error("Failed to connect to MultiVAC RPC at %s: %s", self.rpc_url, exc)
            raise MTVConnectionError(str(exc)) from exc

        logger.info("Connected to MultiVAC RPC %s (chain_id=%s)", self.rpc_url, self._w3.eth.chain_id)
        return self._w3

    def is_connected(self) -> bool:
        if self._w3 is None:
            return False
        try:
            return bool(self._w3.is_connected())
        except Exception:
            return False

    def get_native_balance(self, address: str) -> NativeBalance:
        """Fetch the native MTV coin balance for an address."""
        checksummed = validate_address(address)
        w3 = self.connect()

        def _do_call() -> int:
            return w3.eth.get_balance(checksummed)

        raw = call_with_retry(
            _do_call,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            retry_on=(Web3Exception, ConnectionError, TimeoutError, OSError),
            what=f"get_balance({checksummed})",
        )
        balance = Decimal(raw) / (Decimal(10) ** config.NATIVE_DECIMALS)
        return NativeBalance(
            address=checksummed, raw_wei=raw, balance=balance, symbol=config.NATIVE_SYMBOL
        )
