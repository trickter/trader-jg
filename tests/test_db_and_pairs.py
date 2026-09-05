from datetime import timedelta

from radar.adapters import normalize_pair, primary_pair
from radar.collector import Collector
from radar.db import Database, iso, utcnow


def test_evm_address_is_case_insensitive_but_solana_is_not(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    now = iso()
    assert db.upsert_token("bsc", "0xAbC", "A", None, None, now) == db.upsert_token("bsc", "0xabc", "A", None, None, now)
    assert db.upsert_token("solana", "AbC", "S", None, None, now) != db.upsert_token("solana", "abc", "S", None, None, now)


def test_observation_window_survives_restart_and_relisting_extends_it(tmp_path):
    path = tmp_path / "test.db"
    db = Database(path)
    db.initialize()
    old = iso(utcnow() - timedelta(hours=73))
    token = db.upsert_token("bsc", "0x1", "A", None, None, old)
    assert db.active_tokens(72) == []
    restarted = Database(path)
    fresh = iso()
    assert restarted.upsert_token("bsc", "0x1", "A", None, None, fresh) == token
    assert len(restarted.active_tokens(72)) == 1


def test_primary_pair_uses_highest_liquidity_and_keeps_windows_separate():
    low = {"pairAddress": "low", "liquidity": {"usd": 10}, "volume": {"h1": 2, "h6": 7}, "txns": {"h1": {"buys": 1, "sells": 2}}}
    high = {"pairAddress": "high", "liquidity": {"usd": 100}, "volume": {"h1": 20, "h6": 70}, "txns": {"h1": {"buys": 3, "sells": 4}}}
    normalized = normalize_pair(primary_pair([low, high]), iso())
    assert normalized["pair_address"] == "high"
    assert normalized["volume_h1_usd"] == 20
    assert normalized["volume_h6_usd"] == 70
    assert normalized["tx_h1"] == 7


def test_gmgn_six_hour_metrics_fill_missing_market_data_without_overwriting_dex():
    snapshot = {"observed_at": iso(), "price_usd": 1.0, "data_source": "dexscreener"}
    Collector._merge_gmgn(snapshot, ({"price": 2, "market_cap": "300", "liquidity": 40, "volume": 600, "swaps": 12}, iso()))
    assert snapshot["price_usd"] == 1.0
    assert snapshot["market_cap_usd"] == 300
    assert snapshot["liquidity_usd"] == 40
    assert snapshot["volume_h6_usd"] == 600
    assert snapshot["tx_h6"] == 12
    assert snapshot["data_source"] == "gmgn"


def test_pair_switch_is_recorded(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    token = db.upsert_token("bsc", "0x1", "A", None, None, iso())
    db.save_snapshot(token, {"observed_at": iso(), "pair_address": "pair-a", "data_source": "dexscreener"})
    db.save_snapshot(token, {"observed_at": iso(), "pair_address": "pair-b", "data_source": "dexscreener"})
    with db.connect() as conn:
        row = conn.execute("SELECT from_pair,to_pair FROM pair_switches").fetchone()
    assert tuple(row) == ("pair-a", "pair-b")


def test_market_metadata_fill_does_not_extend_listing_lifetime(tmp_path):
    db = Database(tmp_path / "test.db")
    db.initialize()
    old = iso(utcnow() - timedelta(hours=73))
    token = db.upsert_token("bsc", "0x1", None, None, None, old)
    db.update_token_metadata(token, "ABC", "Alpha Beta", "https://example.test/a.png")
    with db.connect() as conn:
        row = conn.execute("SELECT symbol,name,logo_url,last_seen_at FROM tokens WHERE id=?", (token,)).fetchone()
    assert tuple(row) == ("ABC", "Alpha Beta", "https://example.test/a.png", old)
    assert db.active_tokens(72) == []
