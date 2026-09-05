import httpx

from radar.adapters import BinanceAdapter


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
