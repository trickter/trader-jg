import httpx

from radar.adapters import BinanceAdapter, GeckoTerminalAdapter, GmgnAdapter, SourceError
from radar.config import Settings


async def test_binance_relative_icon_and_alpha_parameters_are_normalized():
    payload = {"code": "000000", "data": {"tokens": [{"contractAddress": "0xA", "symbol": "ABC", "icon": "relative/icon.png"}]}}

    def handler(request: httpx.Request):
        assert request.method == "POST"
        body = __import__("json").loads(request.content)
        assert body["rankType"] == 20
        assert body["period"] == 30
        assert body["sortBy"] == 70
        assert body["volumeMin"] == 0
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items, _, _ = await BinanceAdapter(client).alpha("bsc")
    assert items[0]["logo"] == "https://bin.bnbstatic.com/relative/icon.png"


async def test_gmgn_trending_uses_openapi_auth_and_normalizes_rank(monkeypatch):
    monkeypatch.delenv("GMGN_API_KEY", raising=False)
    payload = {
        "code": 0,
        "data": {
            "code": 0,
            "data": {
                "rank": [
                    {"address": "SoLAddress", "symbol": "HOT", "name": "Hot Token", "logo": "https://img.example/hot.png", "rank": 3}
                ]
            },
        },
    }

    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.url.path == "/v1/market/rank"
        assert request.url.params["chain"] == "sol"
        assert request.url.params["interval"] == "6h"
        assert request.url.params["limit"] == "20"
        assert request.url.params["order_by"] == "default"
        assert request.url.params["timestamp"]
        assert request.url.params["client_id"]
        assert request.headers["x-apikey"] == "gmgn_solbscbaseethmonadtron"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        items, params, raw = await GmgnAdapter(client, Settings.from_env()).trending("solana", "6h")
    assert items == [{"chain": "solana", "address": "SoLAddress", "symbol": "HOT", "name": "Hot Token", "logo": "https://img.example/hot.png", "rank": 3, "metrics": payload["data"]["data"]["rank"][0]}]
    assert "timestamp" not in params and "client_id" not in params
    assert raw == payload


async def test_geckoterminal_ohlcv_validates_base_token_and_sorts_bars():
    payload = {
        "meta": {"base": {"address": "0xABC"}},
        "data": {"attributes": {"ohlcv_list": [
            [200, 2, 3, 1, 2.5, 20],
            [100, 1, 2, .5, 1.5, 10],
            [300, 3, 4, 2, 3.5, 30],
        ]}},
    }

    def handler(request: httpx.Request):
        assert request.url.path == "/api/v2/networks/bsc/pools/pair/ohlcv/hour"
        assert request.url.params["token"] == "base"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        bars, _ = await GeckoTerminalAdapter(client).ohlcv("bsc", "pair", "0xabc", 300)
    assert [bar["close"] for bar in bars] == [1.5, 2.5]


async def test_geckoterminal_rejects_pool_identity_mismatch():
    payload = {"meta": {"base": {"address": "wrong"}}, "data": {"attributes": {"ohlcv_list": []}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        try:
            await GeckoTerminalAdapter(client).ohlcv("solana", "pair", "expected", 300)
        except SourceError as exc:
            assert exc.code == "IDENTITY_MISMATCH"
        else:
            raise AssertionError("identity mismatch must fail closed")
