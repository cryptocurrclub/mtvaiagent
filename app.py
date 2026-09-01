"""
MTV Portfolio Dashboard - Streamlit entry point.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
from blockchain import InvalidAddressError, MTVConnectionError, MultiVacClient, NativeBalance, validate_address
from price_api import CurrentPrice, PriceAPIError, PriceHistory, PriceService
from staking_api import StakingAPIError, StakingApiClient, StakingSummary

config.configure_logging()
logger = logging.getLogger(__name__)

st.set_page_config(page_title="MTV Portfolio Dashboard", page_icon="\U0001FA99", layout="wide")


# --- Cached resources / data loaders ---------------------------------------


@st.cache_resource(show_spinner=False)
def get_client(rpc_url: str) -> MultiVacClient:
    return MultiVacClient(rpc_url=rpc_url)


@st.cache_resource(show_spinner=False)
def get_price_service() -> PriceService:
    return PriceService()


@st.cache_resource(show_spinner=False)
def get_staking_client() -> StakingApiClient:
    return StakingApiClient()


@st.cache_data(ttl=config.BALANCE_CACHE_TTL_SECONDS, show_spinner=False)
def load_balance(rpc_url: str, address: str) -> NativeBalance:
    return get_client(rpc_url).get_native_balance(address)


@st.cache_data(ttl=config.BALANCE_CACHE_TTL_SECONDS, show_spinner=False)
def load_staking_summary(address: str) -> StakingSummary:
    return get_staking_client().get_staking_summary(address)


@st.cache_data(ttl=config.CURRENT_PRICE_CACHE_TTL_SECONDS, show_spinner=False)
def load_current_price(vs_currency: str) -> CurrentPrice:
    return get_price_service().get_current_price(vs_currency)


@st.cache_data(ttl=config.HISTORY_CACHE_TTL_SECONDS, show_spinner=False)
def load_price_history(days: int, vs_currency: str) -> PriceHistory:
    return get_price_service().get_market_chart(days, vs_currency)


# --- Sidebar: configuration --------------------------------------------------

st.sidebar.header("Configuration")
rpc_url = st.sidebar.text_input("RPC URL", value=config.RPC_URL)
wallet_input = st.sidebar.text_input("Wallet address", value=config.WALLET_ADDRESS)
vs_currency = st.sidebar.selectbox("Currency", ["usd", "eur", "eth", "btc"], index=0)
period_label = st.sidebar.selectbox(
    "Price history period",
    list(config.TIME_PERIODS.keys()),
    index=list(config.TIME_PERIODS.keys()).index(config.DEFAULT_PERIOD),
)
period_days = config.TIME_PERIODS[period_label]

if st.sidebar.button("Refresh data", type="primary"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(
    "Staking data comes from MultiVAC's explorer API (e.mtv.ac) - MultiVAC's native-chain "
    "staking has no smart contract, so it can't be read via eth_call. See staking_api.py."
)

# --- Validate wallet address up front ----------------------------------------

try:
    wallet_address = validate_address(wallet_input)
    address_error = None
except InvalidAddressError as exc:
    wallet_address = None
    address_error = str(exc)

# --- Header: title, connection status, last updated --------------------------

title_col, status_col = st.columns([3, 1])
with title_col:
    st.title("MTV Portfolio Dashboard")
with status_col:
    client = get_client(rpc_url)
    try:
        client.connect()
        st.success(f"Connected (chain id {client._w3.eth.chain_id})", icon="\U0001F7E2")
    except MTVConnectionError as exc:
        st.error("RPC connection failed", icon="\U0001F534")
        st.caption(str(exc))

st.caption(f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

if address_error:
    st.error(f"Invalid wallet address: {address_error}")
    st.stop()

# --- Fetch data (with loading states + friendly errors) ----------------------

balance: NativeBalance | None = None
staking: StakingSummary | None = None
price: CurrentPrice | None = None
history: PriceHistory | None = None

with st.spinner("Fetching on-chain balance..."):
    try:
        balance = load_balance(rpc_url, wallet_address)
    except MTVConnectionError as exc:
        st.error(f"Could not fetch balance - RPC connection problem: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error fetching balance")
        st.error(f"Unexpected error fetching balance: {exc}")

with st.spinner("Fetching staking data..."):
    try:
        staking = load_staking_summary(wallet_address)
    except StakingAPIError as exc:
        st.warning(f"Staking data unavailable: {exc}")
    except Exception as exc:
        logger.exception("Unexpected error fetching staking data")
        st.warning(f"Unexpected error fetching staking data: {exc}")

with st.spinner("Fetching current price..."):
    try:
        price = load_current_price(vs_currency)
    except PriceAPIError as exc:
        st.warning(f"Price data unavailable: {exc}")

with st.spinner("Fetching price history..."):
    try:
        history = load_price_history(period_days, vs_currency)
    except PriceAPIError as exc:
        st.warning(f"Price history unavailable: {exc}")

# --- Metrics row ---------------------------------------------------------------

st.subheader("Portfolio")
m1, m2, m3, m4 = st.columns(4)

if balance:
    m1.metric(f"Wallet balance ({balance.symbol})", f"{balance.balance:,.4f}")
else:
    m1.metric(f"Wallet balance ({config.NATIVE_SYMBOL})", "-")

if price:
    price_label = f"{price.vs_currency.upper()} price"
    m2.metric(price_label, f"{price.price:,.6f}", help=f"Source: {price.source}")
else:
    m2.metric("Price", "-")

portfolio_value = balance.balance if balance else Decimal(0)
if staking:
    portfolio_value += staking.total_staked

if price and (balance or staking):
    est_value = portfolio_value * Decimal(str(price.price))
    m3.metric(f"Estimated value ({vs_currency.upper()})", f"{est_value:,.2f}", help="Wallet balance + total staked, at current price")
else:
    m3.metric("Estimated value", "-")

if staking:
    m4.metric(f"Total staked ({config.NATIVE_SYMBOL})", f"{staking.total_staked:,.0f}")
else:
    m4.metric("Total staked", "-")

# --- Staking section -------------------------------------------------------------

st.subheader("Staking")
st.caption(
    "MultiVAC's native staking has no smart contract - these figures come from the "
    "explorer's own (undocumented) backend API, not an on-chain read."
)
if staking is None:
    st.info("Staking data unavailable.")
else:
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Total staked", f"{staking.total_staked:,.4f} {config.NATIVE_SYMBOL}")
    s2.metric("Rank", f"#{staking.rank}" if staking.rank is not None else "-")
    s3.metric(
        "Current APR",
        f"{staking.apr_percent:.2f}%" if staking.apr_percent is not None else "-",
        help="Network-wide rate, not specific to this wallet",
    )
    s4.metric("Withdraw pending", f"{staking.withdraw_pending:,.4f} {config.NATIVE_SYMBOL}")

    with st.expander("Staking breakdown by chain"):
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Mainnet (native)", f"{staking.mainnet_staked:,.4f}")
        b2.metric("ERC-20 (Ethereum)", f"{staking.erc20_staked:,.4f}")
        b3.metric("BEP-20 (BSC)", f"{staking.bep20_staked:,.4f}")
        b4.metric("Withdrawn to date", f"{staking.withdraw_success:,.4f}")

    st.caption(f"Staking data source: {staking.source}{' (cached)' if staking.from_cache else ''}")

# --- Price chart -------------------------------------------------------------------

st.subheader(f"MTV price history ({period_label})")

if history is not None and not history.df.empty:
    df = history.df
    has_volume = "volume" in df.columns and df["volume"].notna().any()

    if has_volume:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.05,
        )
        fig.add_trace(
            go.Scatter(x=df["timestamp"], y=df["price"], mode="lines", name="Price", hovertemplate="%{y:.6f}"),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Bar(x=df["timestamp"], y=df["volume"], name="Volume", marker_opacity=0.4),
            row=2,
            col=1,
        )
    else:
        fig = make_subplots(rows=1, cols=1)
        fig.add_trace(
            go.Scatter(x=df["timestamp"], y=df["price"], mode="lines", name="Price", hovertemplate="%{y:.6f}"),
            row=1,
            col=1,
        )

    fig.update_layout(
        hovermode="x unified",
        margin=dict(l=10, r=10, t=30, b=10),
        height=500,
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1D", step="day", stepmode="backward"),
                dict(count=7, label="7D", step="day", stepmode="backward"),
                dict(count=30, label="30D", step="day", stepmode="backward"),
                dict(count=90, label="90D", step="day", stepmode="backward"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(step="all", label="All"),
            ]
        ),
        rangeslider=dict(visible=True, thickness=0.08),
        row=1,
        col=1,
    )
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Price data source: {history.source}{' (cached)' if history.from_cache else ''}")
else:
    st.info("No price history to display.")
