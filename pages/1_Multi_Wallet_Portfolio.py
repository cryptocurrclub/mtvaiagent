"""
Multi-Wallet Portfolio - combined wallet balance + staking across the
addresses configured in config.PORTFOLIO_WALLET_ADDRESSES.

Mirrors app.py's data-loading and layout conventions (cached resources,
retry-backed fetches, metric cards) so the two pages feel like one app.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import streamlit as st

import config
from blockchain import InvalidAddressError, MTVConnectionError, MultiVacClient, NativeBalance, validate_address
from price_api import CurrentPrice, PriceAPIError, PriceService
from staking_api import StakingAPIError, StakingApiClient, StakingSummary

config.configure_logging()
logger = logging.getLogger(__name__)

st.set_page_config(page_title="MTV Multi-Wallet Portfolio", page_icon="\U0001FA99", layout="wide")


# --- Cached resources / data loaders (same pattern as app.py) ----------------


@st.cache_resource(show_spinner=False)
def get_client(rpc_url: str) -> MultiVacClient:
    return MultiVacClient(rpc_url=rpc_url)


@st.cache_resource(show_spinner=False)
def get_staking_client() -> StakingApiClient:
    return StakingApiClient()


@st.cache_resource(show_spinner=False)
def get_price_service() -> PriceService:
    return PriceService()


@st.cache_data(ttl=config.BALANCE_CACHE_TTL_SECONDS, show_spinner=False)
def load_balance(rpc_url: str, address: str) -> NativeBalance:
    return get_client(rpc_url).get_native_balance(address)


@st.cache_data(ttl=config.BALANCE_CACHE_TTL_SECONDS, show_spinner=False)
def load_staking_summary(address: str) -> StakingSummary:
    return get_staking_client().get_staking_summary(address)


@st.cache_data(ttl=config.CURRENT_PRICE_CACHE_TTL_SECONDS, show_spinner=False)
def load_current_price(vs_currency: str) -> CurrentPrice:
    return get_price_service().get_current_price(vs_currency)


def truncate_address(address: str) -> str:
    return f"{address[:6]}...{address[-5:]}"


@dataclass
class WalletRow:
    address: str
    balance: Optional[Decimal]
    staked: Optional[Decimal]
    combined: Optional[Decimal]
    staking_source: Optional[str]
    staking_from_cache: bool
    balance_error: Optional[str]
    staking_error: Optional[str]


# --- Sidebar -------------------------------------------------------------------

st.sidebar.header("Configuration")
rpc_url = st.sidebar.text_input("RPC URL", value=config.RPC_URL, key="mw_rpc_url")
vs_currency = st.sidebar.selectbox("Currency", ["usd", "eur", "eth", "btc"], index=0, key="mw_vs_currency")

if st.sidebar.button("Refresh data", type="primary", key="mw_refresh"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(
    f"Tracking {len(config.PORTFOLIO_WALLET_ADDRESSES)} wallets. Configure the list via the "
    "MTV_PORTFOLIO_WALLETS environment variable (comma-separated addresses)."
)
st.sidebar.caption(
    "Staking figures come from MultiVAC's explorer API (e.mtv.ac) - see staking_api.py."
)

# --- Validate configured addresses --------------------------------------------

wallet_addresses: list[str] = []
invalid_addresses: list[tuple[str, str]] = []
for raw in config.PORTFOLIO_WALLET_ADDRESSES:
    try:
        wallet_addresses.append(validate_address(raw))
    except InvalidAddressError as exc:
        invalid_addresses.append((raw, str(exc)))

# --- Header --------------------------------------------------------------------

title_col, status_col = st.columns([3, 1])
with title_col:
    st.title("Multi-Wallet Portfolio")
    st.caption("Combined wallet balance and staking across tracked addresses")
with status_col:
    client = get_client(rpc_url)
    try:
        client.connect()
        st.success(f"Connected (chain id {client._w3.eth.chain_id})", icon="\U0001F7E2")
    except MTVConnectionError as exc:
        st.error("RPC connection failed", icon="\U0001F534")
        st.caption(str(exc))

st.caption(f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

if invalid_addresses:
    for raw, err in invalid_addresses:
        st.error(f"Skipping invalid configured address '{raw}': {err}")

if not wallet_addresses:
    st.warning("No valid wallet addresses configured.")
    st.stop()

# --- Fetch current price (shared across all wallets) --------------------------

price: Optional[CurrentPrice] = None
with st.spinner("Fetching current price..."):
    try:
        price = load_current_price(vs_currency)
    except PriceAPIError as exc:
        st.warning(f"Price data unavailable: {exc}")

# --- Fetch per-wallet balance + staking data -----------------------------------

rows: list[WalletRow] = []
with st.spinner(f"Fetching data for {len(wallet_addresses)} wallets..."):
    for addr in wallet_addresses:
        balance_val: Optional[Decimal] = None
        staked_val: Optional[Decimal] = None
        staking_source: Optional[str] = None
        staking_from_cache = False
        balance_error: Optional[str] = None
        staking_error: Optional[str] = None

        try:
            balance_val = load_balance(rpc_url, addr).balance
        except MTVConnectionError as exc:
            balance_error = f"RPC connection problem: {exc}"
        except Exception as exc:
            logger.exception("Unexpected error fetching balance for %s", addr)
            balance_error = str(exc)

        try:
            staking = load_staking_summary(addr)
            staked_val = staking.total_staked
            staking_source = staking.source
            staking_from_cache = staking.from_cache
        except StakingAPIError as exc:
            staking_error = str(exc)
        except Exception as exc:
            logger.exception("Unexpected error fetching staking data for %s", addr)
            staking_error = str(exc)

        combined_val = None
        if balance_val is not None or staked_val is not None:
            combined_val = (balance_val or Decimal(0)) + (staked_val or Decimal(0))

        rows.append(
            WalletRow(
                address=addr,
                balance=balance_val,
                staked=staked_val,
                combined=combined_val,
                staking_source=staking_source,
                staking_from_cache=staking_from_cache,
                balance_error=balance_error,
                staking_error=staking_error,
            )
        )

# --- Aggregate summary -----------------------------------------------------------

grand_balance = sum((r.balance for r in rows if r.balance is not None), Decimal(0))
grand_staked = sum((r.staked for r in rows if r.staked is not None), Decimal(0))
grand_combined = grand_balance + grand_staked
wallets_with_errors = sum(1 for r in rows if r.balance_error or r.staking_error)

st.subheader("Combined summary")
sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric(f"Combined wallet balance ({config.NATIVE_SYMBOL})", f"{grand_balance:,.4f}")
sc2.metric(f"Combined staked ({config.NATIVE_SYMBOL})", f"{grand_staked:,.4f}")
sc3.metric(f"Grand total ({config.NATIVE_SYMBOL})", f"{grand_combined:,.4f}")
if price:
    grand_value = grand_combined * Decimal(str(price.price))
    sc4.metric(
        f"Estimated value ({vs_currency.upper()})",
        f"{grand_value:,.2f}",
        help=f"Source: {price.source}",
    )
else:
    sc4.metric("Estimated value", "-")

if wallets_with_errors:
    st.warning(
        f"{wallets_with_errors} of {len(rows)} wallet(s) had a data-fetch error - see the "
        "affected card below for details."
    )

# --- Per-wallet cards --------------------------------------------------------------
# st.columns reflows to a single stacked column on narrow/mobile viewports.

st.subheader("Wallets")
num_cols = min(len(rows), 3)
cols = st.columns(num_cols)
for i, row in enumerate(rows):
    col = cols[i % num_cols]
    with col:
        with st.container(border=True):
            st.markdown(f"**{truncate_address(row.address)}**", help=row.address)

            if row.balance_error:
                st.error(f"Balance: {row.balance_error}")
            if row.staking_error:
                st.warning(f"Staking: {row.staking_error}")

            st.metric(
                f"Wallet balance ({config.NATIVE_SYMBOL})",
                f"{row.balance:,.4f}" if row.balance is not None else "-",
            )
            st.metric(
                f"Staked ({config.NATIVE_SYMBOL})",
                f"{row.staked:,.4f}" if row.staked is not None else "-",
            )
            st.metric(
                f"Combined ({config.NATIVE_SYMBOL})",
                f"{row.combined:,.4f}" if row.combined is not None else "-",
            )
            if price and row.combined is not None:
                est_value = row.combined * Decimal(str(price.price))
                st.metric(f"Est. value ({vs_currency.upper()})", f"{est_value:,.2f}")

            if row.staking_source:
                st.caption(
                    f"Staking source: {row.staking_source}"
                    f"{' (cached)' if row.staking_from_cache else ''}"
                )

            with st.expander("Full address"):
                st.code(row.address, language=None)
