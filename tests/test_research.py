from datetime import datetime, timedelta, timezone

from radar.db import Database, iso, utcnow
from radar.research import DEFAULT_STRATEGY, replay_bars


def _bar(hour: int, close: float, *, open_: float | None = None,
         high: float | None = None, low: float | None = None) -> dict:
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    open_value = close if open_ is None else open_
    return {
        "open_time": iso(moment), "open": open_value,
        "high": max(open_value, close) if high is None else high,
        "low": min(open_value, close) if low is None else low,
        "close": close, "volume": 1_000, "is_closed": 1,
    }


def test_touch_entry_uses_only_prior_high_and_next_bar_open():
    bars = [_bar(hour, 150, high=151, low=149) for hour in range(24)]
    bars += [
        _bar(24, 95, open_=105, high=106, low=94),
        _bar(25, 100, high=127, low=90),
        _bar(26, 300, high=301, low=299),
    ]
    signals = replay_bars(bars)
    touch = next(signal for signal in signals if signal["entry_type"] == "touch" and signal["level_ratio"] > .6)
    assert touch["high_price"] == 150
    assert touch["level_price"] == 100
    assert touch["entry_time"] == bars[25]["open_time"]
    assert touch["entry_price"] == 100
    assert touch["outcome"] == "WIN"
    assert round(touch["net_return"], 6) == .25


def test_same_hour_target_and_stop_is_ambiguous_and_excluded_from_return():
    bars = [_bar(hour, 150, high=151, low=149) for hour in range(24)]
    bars += [_bar(24, 95, open_=105, high=106, low=94), _bar(25, 100, high=127, low=75)]
    signal = next(signal for signal in replay_bars(bars) if signal["entry_type"] == "touch")
    assert signal["outcome"] == "AMBIGUOUS"
    assert signal["ambiguous"] == 1
    assert signal["net_return"] is None


def test_verified_token_remains_tracked_after_listing_window(tmp_path):
    database = Database(tmp_path / "research.db")
    database.initialize()
    old = iso(utcnow() - timedelta(days=30))
    token_id = database.upsert_token("bsc", "0x123", "HIGH", None, None, old)
    database.upsert_qualification(token_id, "VERIFIED", 40_000_000, old, "test", "TEST", 1.0)
    assert database.active_tokens(72) == []
    assert [row["id"] for row in database.tracked_tokens(72)] == [token_id]


def test_strategy_defaults_match_research_definition():
    assert DEFAULT_STRATEGY["levels"] == [2 / 3, 1 / 3, 1 / 6]
    assert DEFAULT_STRATEGY["take_profit"] == DEFAULT_STRATEGY["stop_loss"] == .25
    assert DEFAULT_STRATEGY["holding_hours"] == 168


def test_failed_backfill_job_resumes_from_saved_cursor(tmp_path):
    database = Database(tmp_path / "jobs.db")
    database.initialize()
    token_id = database.upsert_token("bsc", "0x456", "JOB", None, None, iso())
    job_id, cursor = database.start_backfill_job(token_id, "geckoterminal", "1h", "start", "end", 300)
    assert cursor == 300
    database.update_backfill_job(job_id, "failed", 200, "rate limited", 300)
    resumed_id, resumed_cursor = database.start_backfill_job(token_id, "geckoterminal", "1h", "start", "end", 300)
    assert resumed_id == job_id
    assert resumed_cursor == 200
