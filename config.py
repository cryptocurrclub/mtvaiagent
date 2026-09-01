"""
Central configuration for the MTV portfolio dashboard.

Every value here can be overridden with an environment variable of the
same name (see .env.example), so the app can be pointed at a different
RPC endpoint, wallet address, or staking contract without touching code.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv is optional; env vars set some other way still work.
    pass

BASE_DIR = Path(__file__).resolve().parent

# --- Blockchain connection -------------------------------------------------

RPC_URL = os.environ.get("MTV_RPC_URL", "https://rpc.mtv.ac")
CHAIN_ID = int(os.environ.get("MTV_CHAIN_ID", "62621"))
WALLET_ADDRESS = os.environ.get(
    "MTV_WALLET_ADDRESS", "0xecff9107e7193ac490a4694b2db27c849a62446a"
)

# Wallets tracked by the multi-wallet portfolio page. Comma-separated env
# override so the tracked set can change without touching code.
_DEFAULT_PORTFOLIO_WALLETS = ",".join(
    [
        "0xecff9107e7193ac490a4694b2db27c849a62446a",
        "0x2cc552f5d43af02aac50b2e959888dfe42af368c",
        "0x53ee00c9f314a78a100a349dd4f7116870698d30",
    ]
)
PORTFOLIO_WALLET_ADDRESSES = [
    a.strip()
    for a in os.environ.get("MTV_PORTFOLIO_WALLETS", _DEFAULT_PORTFOLIO_WALLETS).split(",")
    if a.strip()
]

# Decimal places for the native MTV coin. MultiVAC's mainnet RPC is
# Ethereum-JSON-RPC compatible and eth_getBalance returns a wei-style
# integer; 18 decimals is the standard EVM assumption. Verify against an
# explorer before trusting this for financial decisions.
NATIVE_DECIMALS = int(os.environ.get("MTV_NATIVE_DECIMALS", "18"))
NATIVE_SYMBOL = os.environ.get("MTV_NATIVE_SYMBOL", "MTV")

# --- Staking data ------------------------------------------------------------
#
# MultiVAC's native-chain staking is NOT a smart contract: per the staking
# portal's own client code (https://e.mtv.ac/staking.html), "staking" is a
# plain native-coin transfer to a fixed system address, and per-address
# staked totals / rank are tracked off-chain by the explorer backend and
# served over this REST API - not readable via eth_call. See staking_api.py.
#
# Both values below were reverse-engineered from the portal's obfuscated JS
# bundle (undocumented, unversioned, may change without notice):
#   - base URL: the explorer backend the staking portal itself calls
#   - APR numerator: the portal computes displayed APR as
#     `numerator / stake_count`, i.e. it scales inversely with total staked.
STAKING_API_BASE_URL = os.environ.get("MTV_STAKING_API_BASE_URL", "https://e.mtv.ac")
STAKING_APR_NUMERATOR = float(os.environ.get("MTV_STAKING_APR_NUMERATOR", "6570000000"))

# --- Price API ---------------------------------------------------------------

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_COIN_ID = os.environ.get("MTV_COINGECKO_ID", "multivac")

CRYPTOCOMPARE_BASE_URL = "https://min-api.cryptocompare.com/data"
CRYPTOCOMPARE_SYMBOL = os.environ.get("MTV_CRYPTOCOMPARE_SYMBOL", "MTV")

DEFAULT_VS_CURRENCY = os.environ.get("MTV_VS_CURRENCY", "usd")

# Time-period selector: label -> days of history
TIME_PERIODS = {
    "1D": 1,
    "7D": 7,
    "30D": 30,
    "90D": 90,
    "1Y": 365,
}
DEFAULT_PERIOD = os.environ.get("MTV_DEFAULT_PERIOD", "30D")

# --- Caching / networking -----------------------------------------------------

REQUEST_TIMEOUT_SECONDS = float(os.environ.get("MTV_REQUEST_TIMEOUT", "10"))
MAX_RETRIES = int(os.environ.get("MTV_MAX_RETRIES", "4"))
RETRY_BACKOFF_BASE_SECONDS = float(os.environ.get("MTV_RETRY_BACKOFF_BASE", "0.5"))

CURRENT_PRICE_CACHE_TTL_SECONDS = int(os.environ.get("MTV_PRICE_CACHE_TTL", "60"))
HISTORY_CACHE_TTL_SECONDS = int(os.environ.get("MTV_HISTORY_CACHE_TTL", "300"))
BALANCE_CACHE_TTL_SECONDS = int(os.environ.get("MTV_BALANCE_CACHE_TTL", "30"))

CACHE_DIR = BASE_DIR / ".cache"

# --- Logging -------------------------------------------------------------------

LOG_LEVEL = os.environ.get("MTV_LOG_LEVEL", "INFO")


def configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
