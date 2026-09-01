"""Historical + current MTV price data, with caching and provider fallback.

Primary source is CoinGecko; if it's unreachable or rate-limited, we fall
back to CryptoCompare. Both responses are normalized to the same shape so
the UI layer doesn't need to know which provider answered.
"""
from __future__ import annotations

import io
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import requests

import config
from cache import DiskCache
from retry import call_with_retry

logger = logging.getLogger(__name__)

RETRYABLE_EXCEPTIONS = (requests.ConnectionError, requests.Timeout, requests.HTTPError)


class PriceAPIError(Exception):
    """Raised when no configured price provider could satisfy a request."""


@dataclass
class PriceHistory:
    df: pd.DataFrame  # columns: timestamp (datetime64), price, volume (nullable)
    source: str
    fetched_at: float
    from_cache: bool = False


@dataclass
class CurrentPrice:
    price: float
    vs_currency: str
    source: str
    fetched_at: float
    from_cache: bool = False


class PriceProvider(ABC):
    name: str

    @abstractmethod
    def fetch_current_price(self, vs_currency: str) -> float:
        ...

    @abstractmethod
    def fetch_market_chart(self, days: int, vs_currency: str) -> pd.DataFrame:
        ...


class CoinGeckoProvider(PriceProvider):
    name = "coingecko"

    def __init__(self, coin_id: str = config.COINGECKO_COIN_ID):
        self.coin_id = coin_id

    def fetch_current_price(self, vs_currency: str) -> float:
        resp = requests.get(
            f"{config.COINGECKO_BASE_URL}/simple/price",
            params={"ids": self.coin_id, "vs_currencies": vs_currency},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return float(data[self.coin_id][vs_currency])
        except (KeyError, TypeError) as exc:
            raise PriceAPIError(f"Unexpected CoinGecko response: {data}") from exc

    def fetch_market_chart(self, days: int, vs_currency: str) -> pd.DataFrame:
        resp = requests.get(
            f"{config.COINGECKO_BASE_URL}/coins/{self.coin_id}/market_chart",
            params={"vs_currency": vs_currency, "days": days},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        prices = data.get("prices", [])
        volumes = dict(data.get("total_volumes", []))
        if not prices:
            raise PriceAPIError(f"Unexpected CoinGecko response: {data}")
        rows = [
            {
                "timestamp": pd.to_datetime(ts, unit="ms"),
                "price": price,
                "volume": volumes.get(ts),
            }
            for ts, price in prices
        ]
        return pd.DataFrame(rows)


class CryptoCompareProvider(PriceProvider):
    name = "cryptocompare"

    def __init__(self, symbol: str = config.CRYPTOCOMPARE_SYMBOL):
        self.symbol = symbol

    def fetch_current_price(self, vs_currency: str) -> float:
        resp = requests.get(
            f"{config.CRYPTOCOMPARE_BASE_URL}/price",
            params={"fsym": self.symbol, "tsyms": vs_currency.upper()},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if vs_currency.upper() not in data:
            raise PriceAPIError(f"Unexpected CryptoCompare response: {data}")
        return float(data[vs_currency.upper()])

    def fetch_market_chart(self, days: int, vs_currency: str) -> pd.DataFrame:
        # histoday for longer ranges, histohour for short/fine-grained ranges.
        if days <= 2:
            endpoint, limit, param_key = "histohour", days * 24, "limit"
        else:
            endpoint, limit, param_key = "histoday", days, "limit"

        resp = requests.get(
            f"{config.CRYPTOCOMPARE_BASE_URL}/v2/{endpoint}",
            params={"fsym": self.symbol, "tsym": vs_currency.upper(), param_key: limit},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("Response") == "Error":
            raise PriceAPIError(f"CryptoCompare error: {payload.get('Message')}")
        rows = [
            {
                "timestamp": pd.to_datetime(point["time"], unit="s"),
                "price": point["close"],
                "volume": point.get("volumeto"),
            }
            for point in payload.get("Data", {}).get("Data", [])
        ]
        if not rows:
            raise PriceAPIError(f"Unexpected CryptoCompare response: {payload}")
        return pd.DataFrame(rows)


class PriceService:
    """Fetches current + historical MTV prices with caching and provider fallback."""

    def __init__(self, providers: Optional[list[PriceProvider]] = None):
        self.providers = providers or [CoinGeckoProvider(), CryptoCompareProvider()]
        self.cache = DiskCache(config.CACHE_DIR)

    def get_current_price(self, vs_currency: str = config.DEFAULT_VS_CURRENCY) -> CurrentPrice:
        cache_key = f"current_{vs_currency}"
        cached = self.cache.get(cache_key, config.CURRENT_PRICE_CACHE_TTL_SECONDS)
        if cached:
            return CurrentPrice(
                price=cached["price"],
                vs_currency=vs_currency,
                source=cached["source"],
                fetched_at=cached["_cached_at"],
                from_cache=True,
            )

        last_exc: Optional[Exception] = None
        for provider in self.providers:
            try:
                price = call_with_retry(
                    lambda p=provider: p.fetch_current_price(vs_currency),
                    max_retries=config.MAX_RETRIES,
                    backoff_base=config.RETRY_BACKOFF_BASE_SECONDS,
                    retry_on=RETRYABLE_EXCEPTIONS,
                    what=f"{provider.name} current price",
                )
                self.cache.set(cache_key, {"price": price, "source": provider.name})
                return CurrentPrice(
                    price=price, vs_currency=vs_currency, source=provider.name, fetched_at=time.time()
                )
            except Exception as exc:
                logger.warning("Provider %s failed for current price: %s", provider.name, exc)
                last_exc = exc

        stale = self.cache.get_stale(cache_key)
        if stale:
            logger.warning("All price providers failed; serving stale cached current price.")
            return CurrentPrice(
                price=stale["price"],
                vs_currency=vs_currency,
                source=f"{stale['source']} (stale cache)",
                fetched_at=stale["_cached_at"],
                from_cache=True,
            )
        raise PriceAPIError(f"All price providers failed: {last_exc}")

    def get_market_chart(
        self, days: int, vs_currency: str = config.DEFAULT_VS_CURRENCY
    ) -> PriceHistory:
        cache_key = f"history_{days}_{vs_currency}"
        cached = self.cache.get(cache_key, config.HISTORY_CACHE_TTL_SECONDS)
        if cached:
            return PriceHistory(
                df=pd.read_json(io.StringIO(cached["df_json"]), orient="split"),
                source=cached["source"],
                fetched_at=cached["_cached_at"],
                from_cache=True,
            )

        last_exc: Optional[Exception] = None
        for provider in self.providers:
            try:
                df = call_with_retry(
                    lambda p=provider: p.fetch_market_chart(days, vs_currency),
                    max_retries=config.MAX_RETRIES,
                    backoff_base=config.RETRY_BACKOFF_BASE_SECONDS,
                    retry_on=RETRYABLE_EXCEPTIONS,
                    what=f"{provider.name} market chart",
                )
                self.cache.set(
                    cache_key, {"df_json": df.to_json(orient="split", date_unit="ms"), "source": provider.name}
                )
                return PriceHistory(df=df, source=provider.name, fetched_at=time.time())
            except Exception as exc:
                logger.warning("Provider %s failed for market chart: %s", provider.name, exc)
                last_exc = exc

        stale = self.cache.get_stale(cache_key)
        if stale:
            logger.warning("All price providers failed; serving stale cached history.")
            return PriceHistory(
                df=pd.read_json(io.StringIO(stale["df_json"]), orient="split"),
                source=f"{stale['source']} (stale cache)",
                fetched_at=stale["_cached_at"],
                from_cache=True,
            )
        raise PriceAPIError(f"All price providers failed: {last_exc}")
