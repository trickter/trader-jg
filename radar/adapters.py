from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Settings


TARGET_CHAINS = {"bsc", "solana", "robinhood"}
BINANCE_CHAINS = {"bsc": "56", "solana": "CT_501"}
OKX_CHAINS = {"bsc": "56", "solana": "501", "robinhood": "4663"}
DEX_CHAINS = {"bsc": "bsc", "solana": "solana", "robinhood": "robinhood"}
GMGN_CHAINS = {"bsc": "bsc", "solana": "sol", "robinhood": "robinhood"}


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def millis_to_iso(value: Any) -> str | None:
    parsed = number(value)
    if parsed is None or parsed <= 0:
        return None
    return datetime.fromtimestamp(parsed / 1000, timezone.utc).isoformat()


@dataclass
class SourceError(Exception):
    message: str
    status: int | None = None
    code: str | None = None

    def __str__(self) -> str:
        return self.message


class BaseAdapter:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def json_request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise SourceError(f"network error: {exc.__class__.__name__}", code="NETWORK") from exc
        if response.status_code == 402:
            raise SourceError("source requires payment; paused without payment", 402, "PAYMENT_REQUIRED")
        if response.status_code == 429:
            raise SourceError("source rate limited", 429, "RATE_LIMITED")
        if response.status_code in (401, 403):
            raise SourceError("source authentication rejected", response.status_code, "AUTH")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SourceError("source returned invalid JSON", response.status_code, "INVALID_JSON") from exc
        if response.is_error:
            raise SourceError(f"source HTTP {response.status_code}", response.status_code, "HTTP_ERROR")
        return payload


class BinanceAdapter(BaseAdapter):
    URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai"

    async def alpha(self, chain: str) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        params = {
            "rankType": 20,
            "chainId": BINANCE_CHAINS[chain],
            "period": 30,
            "sortBy": 70,
            "orderAsc": False,
            "page": 1,
            "size": 20,
            "countMin": 0,
            "launchTimeMin": 0,
            "liquidityMin": 0,
            "uniqueTraderMin": 0,
            "volumeMin": 0,
        }
        payload = await self.json_request("POST", self.URL, json=params, headers={"User-Agent": "hot-coin-radar/0.1"})
        if str(payload.get("code", "000000")) not in ("0", "000000"):
            raise SourceError(payload.get("message") or payload.get("msg") or "Binance business error", code=str(payload.get("code")))
        raw = (payload.get("data") or {}).get("tokens") or []
        items = []
        for rank, token in enumerate(raw[:20], 1):
            address = token.get("contractAddress")
            if not address:
                continue
            icon = token.get("icon")
            if icon and not icon.startswith(("http://", "https://")):
                icon = "https://bin.bnbstatic.com/" + icon.lstrip("/")
            items.append({"chain": chain, "address": address, "symbol": token.get("symbol"), "name": token.get("name"), "logo": icon, "rank": rank, "metrics": token})
        return items, params, payload


class OkxAdapter(BaseAdapter):
    BASE = "https://web3.okx.com"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        super().__init__(client)
        self.settings = settings

    def headers(self, path_with_query: str) -> dict[str, str]:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        prehash = timestamp + "GET" + path_with_query
        signature = base64.b64encode(hmac.new(self.settings.okx_secret_key.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        headers = {
            "OK-ACCESS-KEY": self.settings.okx_api_key or "",
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.settings.okx_passphrase or "",
        }
        if self.settings.okx_project_id:
            headers["OK-ACCESS-PROJECT"] = self.settings.okx_project_id
        return headers

    async def trending(self, chain: str, timeframe: str) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        if not self.settings.okx_configured:
            raise SourceError("OKX credentials are not configured", code="NOT_CONFIGURED")
        params = {"rankingType": "4", "rankBy": "15", "rankingTimeFrame": "2" if timeframe == "1h" else "3", "riskFilter": "false", "chainIndex": OKX_CHAINS[chain], "limit": "20"}
        path = "/api/v6/dex/market/token/hot-token?" + urlencode(params)
        payload = await self.json_request("GET", self.BASE + path, headers=self.headers(path))
        if str(payload.get("code")) != "0":
            raise SourceError(payload.get("msg") or "OKX business error", code=str(payload.get("code")))
        items = []
        for rank, token in enumerate((payload.get("data") or [])[:20], 1):
            address = token.get("tokenContractAddress")
            if not address:
                continue
            items.append({"chain": chain, "address": address, "symbol": token.get("tokenSymbol"), "name": None, "logo": token.get("tokenLogoUrl"), "rank": rank, "metrics": token})
        return items, params, payload


class GmgnAdapter(BaseAdapter):
    BASE = "https://openapi.gmgn.ai"

    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        super().__init__(client)
        self.settings = settings

    async def trending(self, chain: str, interval: str = "1h") -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        params = {
            "chain": GMGN_CHAINS[chain],
            "interval": interval,
            "limit": 20,
            "order_by": "default",
            "direction": "desc",
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "client_id": str(uuid.uuid4()),
        }
        payload = await self.json_request(
            "GET",
            f"{self.BASE}/v1/market/rank",
            params=params,
            headers={"X-APIKEY": self.settings.gmgn_api_key, "User-Agent": "hot-coin-radar/0.1"},
        )
        if str(payload.get("code", "0")) not in ("0", "200"):
            raise SourceError(payload.get("message") or payload.get("msg") or "GMGN business error", code=str(payload.get("code")))
        data = payload.get("data") or {}
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            if str(data.get("code", "0")) not in ("0", "200"):
                raise SourceError(data.get("message") or data.get("reason") or "GMGN business error", code=str(data.get("code")))
            data = data["data"]
        raw = data.get("rank") or []
        items = []
        for position, token in enumerate(raw[:20], 1):
            address = token.get("address")
            if not address:
                continue
            items.append({
                "chain": chain,
                "address": address,
                "symbol": token.get("symbol"),
                "name": token.get("name"),
                "logo": token.get("logo"),
                "rank": integer(token.get("rank")) or position,
                "metrics": token,
            })
        stored_params = {key: value for key, value in params.items() if key not in ("timestamp", "client_id")}
        return items, stored_params, payload


class DexScreenerAdapter(BaseAdapter):
    BASE = "https://api.dexscreener.com"

    async def boosts(self, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any], Any]:
        payload = await self.json_request("GET", f"{self.BASE}/token-boosts/{kind}/v1")
        items = []
        for rank, token in enumerate(payload or [], 1):
            chain = next((name for name, dex_id in DEX_CHAINS.items() if dex_id == token.get("chainId")), None)
            if chain and token.get("tokenAddress"):
                items.append({"chain": chain, "address": token["tokenAddress"], "symbol": None, "name": None, "logo": token.get("icon"), "rank": rank, "metrics": token})
        return items, {}, payload

    async def pairs_for_tokens(self, chain: str, addresses: list[str]) -> tuple[dict[str, list[dict[str, Any]]], Any]:
        if not addresses:
            return {}, []
        url = f"{self.BASE}/tokens/v1/{DEX_CHAINS[chain]}/" + ",".join(addresses)
        payload = await self.json_request("GET", url)
        result: dict[str, list[dict[str, Any]]] = {address: [] for address in addresses}
        key_to_address = {(a if chain == "solana" else a.lower()): a for a in addresses}
        for pair in payload or []:
            base = (pair.get("baseToken") or {}).get("address")
            key = base if chain == "solana" else (base or "").lower()
            if key in key_to_address:
                result[key_to_address[key]].append(pair)
        return result, payload


class GeckoTerminalAdapter(BaseAdapter):
    BASE = "https://api.geckoterminal.com/api/v2"

    async def ohlcv(self, chain: str, pair_address: str, token_address: str,
                    before_timestamp: int, limit: int = 1000) -> tuple[list[dict[str, Any]], Any]:
        payload = await self.json_request(
            "GET",
            f"{self.BASE}/networks/{DEX_CHAINS[chain]}/pools/{pair_address}/ohlcv/hour",
            params={
                "aggregate": 1,
                "limit": min(1000, max(1, limit)),
                "currency": "usd",
                "token": "base",
                "before_timestamp": before_timestamp,
            },
        )
        base = (payload.get("meta") or {}).get("base") or {}
        actual = str(base.get("address") or "")
        expected = token_address if chain == "solana" else token_address.lower()
        comparable = actual if chain == "solana" else actual.lower()
        if comparable != expected:
            raise SourceError("OHLCV pool base token does not match candidate", code="IDENTITY_MISMATCH")
        bars = []
        for item in ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []:
            if len(item) != 6:
                continue
            timestamp, open_, high, low, close, volume = item
            values = [number(value) for value in (open_, high, low, close, volume)]
            if any(value is None for value in values):
                continue
            open_value, high_value, low_value, close_value, volume_value = values
            if timestamp >= before_timestamp or open_value <= 0 or low_value <= 0 or volume_value < 0:
                continue
            if high_value < max(open_value, close_value) or low_value > min(open_value, close_value):
                continue
            bars.append({
                "open_time": datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat(),
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": volume_value,
                "is_closed": True,
            })
        return sorted(bars, key=lambda bar: bar["open_time"]), payload


def primary_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [pair for pair in pairs if pair.get("pairAddress") and number((pair.get("liquidity") or {}).get("usd")) is not None]
    return max(valid, key=lambda pair: number((pair.get("liquidity") or {}).get("usd")) or -1) if valid else None


def normalize_pair(pair: dict[str, Any] | None, observed_at: str) -> dict[str, Any]:
    if not pair:
        return {"observed_at": observed_at, "data_source": "dexscreener"}
    txns = pair.get("txns") or {}
    volumes = pair.get("volume") or {}
    h1 = txns.get("h1") or {}
    m5 = txns.get("m5") or {}
    h6 = txns.get("h6") or {}
    return {
        "observed_at": observed_at,
        "pair_address": pair.get("pairAddress"),
        "dex_id": pair.get("dexId"),
        "quote_symbol": (pair.get("quoteToken") or {}).get("symbol"),
        "price_usd": number(pair.get("priceUsd")),
        "liquidity_usd": number((pair.get("liquidity") or {}).get("usd")),
        "market_cap_usd": number(pair.get("marketCap")),
        "fdv_usd": number(pair.get("fdv")),
        "volume_m5_usd": number(volumes.get("m5")),
        "volume_h1_usd": number(volumes.get("h1")),
        "volume_h6_usd": number(volumes.get("h6")),
        "tx_m5": (integer(m5.get("buys")) or 0) + (integer(m5.get("sells")) or 0) if m5 else None,
        "tx_h1": (integer(h1.get("buys")) or 0) + (integer(h1.get("sells")) or 0) if h1 else None,
        "tx_h6": (integer(h6.get("buys")) or 0) + (integer(h6.get("sells")) or 0) if h6 else None,
        "buys_h1": integer(h1.get("buys")),
        "sells_h1": integer(h1.get("sells")),
        "pair_created_at": millis_to_iso(pair.get("pairCreatedAt")),
        "data_source": "dexscreener",
    }
