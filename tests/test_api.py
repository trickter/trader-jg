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
