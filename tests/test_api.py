import importlib
from datetime import timedelta

from fastapi.testclient import TestClient

from radar.db import iso, utcnow


def test_candidate_views_and_export(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "api.db"))
    import radar.api as api
    api = importlib.reload(api)
    now = iso()
    old = iso(utcnow() - timedelta(hours=73))
    current = api.db.upsert_token("bsc", "0x1", "NOW", None, None, now)
    archived = api.db.upsert_token("bsc", "0x2", "OLD", None, None, old)
    with TestClient(api.app) as client:
        all_rows = client.get("/api/candidates?view=all&include_reject=true").json()
        assert {row["id"] for row in all_rows} == {current, archived}
        active_rows = client.get("/api/candidates?view=active&include_reject=true").json()
        assert [row["id"] for row in active_rows] == [current]
        current_rows = client.get("/api/candidates?view=current&include_reject=true").json()
        assert [row["id"] for row in current_rows] == [current]
        archived_rows = client.get("/api/candidates?view=archived&include_reject=true").json()
        assert [row["id"] for row in archived_rows] == [archived]
        export = client.get("/api/export.csv?view=all")
        assert export.status_code == 200
        assert "NOW" in export.text and "OLD" in export.text


def test_candidate_fuzzy_search_and_market_cap_sort(monkeypatch, tmp_path):
    monkeypatch.setenv("RADAR_DB_PATH", str(tmp_path / "search.db"))
    import radar.api as api
    api = importlib.reload(api)
    now = iso()
    small = api.db.upsert_token("bsc", "0xSmallContract", "SMALL", "Little Moon", None, now)
    large = api.db.upsert_token("bsc", "0xLargeContract", "LARGE", "Super Moon Token", None, now)
    api.db.save_snapshot(small, {"observed_at": now, "market_cap_usd": 100})
    api.db.save_snapshot(large, {"observed_at": now, "market_cap_usd": 1000})

    with TestClient(api.app) as client:
        by_name = client.get("/api/candidates", params={"view": "all", "search": "moon"}).json()
        assert {row["id"] for row in by_name} == {small, large}
        by_symbol = client.get("/api/candidates", params={"view": "all", "search": "larg"}).json()
        assert [row["id"] for row in by_symbol] == [large]
        by_ca = client.get("/api/candidates", params={"view": "all", "search": "smallcontract"}).json()
        assert [row["id"] for row in by_ca] == [small]
        descending = client.get("/api/candidates", params={"view": "all", "sort": "market_cap_desc"}).json()
        assert [row["id"] for row in descending] == [large, small]
        ascending = client.get("/api/candidates", params={"view": "all", "sort": "market_cap_asc"}).json()
        assert [row["id"] for row in ascending] == [small, large]

        first_page = client.get("/api/candidates", params={"view": "all", "sort": "market_cap_desc", "page": 1, "page_size": 1}).json()
        assert first_page["total"] == 2
        assert first_page["pages"] == 2
        assert first_page["page"] == 1
        assert [row["id"] for row in first_page["items"]] == [large]
        assert first_page["status_counts"] == {"PASS": 0, "UNKNOWN": 2, "REJECT": 0}
        second_page = client.get("/api/candidates", params={"view": "all", "sort": "market_cap_desc", "page": 2, "page_size": 1}).json()
        assert [row["id"] for row in second_page["items"]] == [small]
