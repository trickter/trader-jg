from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .adapters import GeckoTerminalAdapter, SourceError
from .config import Settings, load_rules
from .db import Database, iso, utcnow


DEFAULT_STRATEGY = {
    "market_cap_threshold_usd": 30_000_000,
    "supply_change_tolerance": 0.20,
    "levels": [2 / 3, 1 / 3, 1 / 6],
    "lookback_hours": 720,
    "min_history_hours": 24,
    "confirmation_hours": 24,
    "confirmation_breakout_bars": 3,
    "take_profit": 0.25,
    "stop_loss": 0.25,
    "holding_hours": 168,
    "round_trip_cost": 0.01,
}


def strategy_config(settings: Settings) -> dict[str, Any]:
    rules, _ = load_rules(settings.rules_path)
    return {**DEFAULT_STRATEGY, **(rules.get("strategy") or {})}


def config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _result(bars: list[dict[str, Any]], entry_index: int, config: dict[str, Any]) -> dict[str, Any]:
    entry = bars[entry_index]
    entry_price = float(entry["open"])
    cost = float(config["round_trip_cost"])
    target_price = entry_price * (1 + float(config["take_profit"]) + cost)
    stop_price = entry_price * (1 - float(config["stop_loss"]) + cost)
    holding = int(config["holding_hours"])
    end = min(len(bars), entry_index + holding)
    for bar in bars[entry_index:end]:
        hit_target = float(bar["high"]) >= target_price
        hit_stop = float(bar["low"]) <= stop_price
        if hit_target and hit_stop:
            return {
                "exit_time": bar["open_time"], "exit_price": None, "outcome": "AMBIGUOUS",
                "net_return": None, "ambiguous": 1, "note": "同一小时同时触及止盈和止损",
            }
        if hit_target:
            return {
                "exit_time": bar["open_time"], "exit_price": target_price, "outcome": "WIN",
                "net_return": target_price / entry_price - 1 - cost, "ambiguous": 0, "note": None,
            }
        if hit_stop:
            return {
                "exit_time": bar["open_time"], "exit_price": stop_price, "outcome": "LOSS",
                "net_return": stop_price / entry_price - 1 - cost, "ambiguous": 0, "note": None,
            }
    if len(bars) - entry_index < holding:
        return {"exit_time": None, "exit_price": None, "outcome": "OPEN", "net_return": None, "ambiguous": 0, "note": "观察期未结束"}
    last = bars[end - 1]
    exit_price = float(last["close"])
    return {
        "exit_time": last["open_time"], "exit_price": exit_price, "outcome": "TIMEOUT",
        "net_return": exit_price / entry_price - 1 - cost, "ambiguous": 0, "note": None,
    }


def replay_bars(bars: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Create deterministic paper trades from closed hourly bars."""
    config = {**DEFAULT_STRATEGY, **(config or {})}
    bars = sorted((bar for bar in bars if bar.get("is_closed", 1)), key=lambda bar: bar["open_time"])
    minimum = int(config["min_history_hours"])
    lookback = int(config["lookback_hours"])
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, float, str]] = set()
    for index in range(minimum, len(bars) - 1):
        history = bars[max(0, index - lookback):index]
        peak = max(history, key=lambda bar: float(bar["close"]))
        peak_price = float(peak["close"])
        previous = float(bars[index - 1]["close"])
        current = float(bars[index]["close"])
        for ratio in config["levels"]:
            ratio = float(ratio)
            level = peak_price * ratio
            if not (previous > level >= current):
                continue
            for entry_type in ("touch", "confirm"):
                key = (peak["open_time"], ratio, entry_type)
                if key in seen:
                    continue
                seen.add(key)
                entry_index: int | None = index + 1 if entry_type == "touch" else None
                if entry_type == "confirm":
                    last_confirmation = min(len(bars) - 2, index + int(config["confirmation_hours"]))
                    width = int(config["confirmation_breakout_bars"])
                    for candidate in range(index + 1, last_confirmation + 1):
                        prior = bars[max(0, candidate - width):candidate]
                        if len(prior) == width and float(bars[candidate]["close"]) > max(float(bar["high"]) for bar in prior):
                            entry_index = candidate + 1
                            break
                common = {
                    "high_time": peak["open_time"], "high_price": peak_price,
                    "level_ratio": ratio, "level_price": level, "triggered_at": bars[index]["open_time"],
                    "entry_type": entry_type,
                }
                if entry_index is None:
                    results.append({**common, "entry_time": None, "entry_price": None, "exit_time": None,
                                    "exit_price": None, "outcome": "UNCONFIRMED", "net_return": None,
                                    "ambiguous": 0, "note": "24小时内未确认"})
                    continue
                entry = bars[entry_index]
                results.append({**common, "entry_time": entry["open_time"], "entry_price": float(entry["open"]),
                                **_result(bars, entry_index, config)})
    return results


class ResearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.db.initialize()
        self.config = strategy_config(settings)

    def refresh_qualifications(self) -> dict[str, int]:
        threshold = float(self.config["market_cap_threshold_usd"])
        tolerance = float(self.config["supply_change_tolerance"])
        counts = {"VERIFIED": 0, "PENDING": 0, "EXCLUDED": 0}
        with self.db.connect() as conn:
            tokens = conn.execute("SELECT * FROM tokens").fetchall()
            for token in tokens:
                snapshots = conn.execute(
                    """SELECT observed_at,price_usd,market_cap_usd,data_source FROM market_snapshots
                    WHERE token_id=? AND price_usd>0 AND market_cap_usd>0 ORDER BY observed_at""",
                    (token["id"],),
                ).fetchall()
                if not snapshots:
                    continue
                peak = max(snapshots, key=lambda row: float(row["market_cap_usd"]))
                if float(peak["market_cap_usd"]) < threshold:
                    continue
                label = f"{token['symbol'] or ''} {token['name'] or ''}".lower()
                if "xstock" in label:
                    status, reason, ratio = "EXCLUDED", "TOKENIZED_STOCK", None
                else:
                    supplies = [float(row["market_cap_usd"]) / float(row["price_usd"]) for row in snapshots]
                    median_supply = statistics.median(supplies)
                    peak_supply = float(peak["market_cap_usd"]) / float(peak["price_usd"])
                    ratio = peak_supply / median_supply if median_supply else None
                    if len(supplies) < 2 or ratio is None or abs(ratio - 1) > tolerance:
                        status, reason = "PENDING", "MARKET_CAP_SUPPLY_DISCONTINUITY"
                    else:
                        status, reason = "VERIFIED", "MARKET_CAP_THRESHOLD_MET"
                self.db.upsert_qualification(token["id"], status, float(peak["market_cap_usd"]),
                                             peak["observed_at"], peak["data_source"], reason, ratio)
                counts[status] += 1
        return counts

    async def backfill(self, days: int = 90, limit_tokens: int = 20, token_id: int | None = None) -> dict[str, int]:
        end = utcnow().replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(days=days)
        with self.db.connect() as conn:
            where = "WHERE q.status='VERIFIED'"
            args: list[Any] = []
            if token_id is not None:
                where += " AND t.id=?"
                args.append(token_id)
            rows = conn.execute(
                f"""SELECT t.*,s.pair_address FROM tokens t JOIN research_qualifications q ON q.token_id=t.id
                JOIN market_snapshots s ON s.id=(SELECT id FROM market_snapshots WHERE token_id=t.id AND pair_address IS NOT NULL ORDER BY observed_at DESC LIMIT 1)
                {where} ORDER BY q.peak_market_cap_usd DESC LIMIT ?""",
                (*args, limit_tokens),
            ).fetchall()
        summary = {"tokens": 0, "bars": 0, "failed": 0}
        timeout = httpx.Timeout(30)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            adapter = GeckoTerminalAdapter(client)
            for token in rows:
                token_bars = 0
                job_id, before = self.db.start_backfill_job(
                    token["id"], "geckoterminal", "1h", iso(start), iso(end), int(end.timestamp()),
                )
                try:
                    while before > int(start.timestamp()):
                        bars, payload = await adapter.ohlcv(token["chain"], token["pair_address"], token["address"], before)
                        raw_page = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
                        bars = [bar for bar in bars if bar["open_time"] >= iso(start)]
                        if not bars:
                            break
                        token_bars += self.db.save_ohlcv_bars(token["id"], "geckoterminal", token["chain"],
                                                             token["pair_address"], "1h", bars)
                        oldest = datetime.fromisoformat(bars[0]["open_time"])
                        next_before = int(oldest.timestamp())
                        if next_before >= before:
                            break
                        before = next_before
                        self.db.update_backfill_job(job_id, "running", before)
                        if oldest <= start or len(raw_page) < 1000:
                            break
                        await asyncio.sleep(1.5)
                    self.db.update_backfill_job(job_id, "complete", before)
                    summary["tokens"] += 1
                    summary["bars"] += token_bars
                except (SourceError, httpx.HTTPError) as exc:
                    retry = 300 if getattr(exc, "status", None) == 429 else 60
                    self.db.update_backfill_job(job_id, "failed", before, str(exc), retry)
                    summary["failed"] += 1
                    await asyncio.sleep(2)
                    if getattr(exc, "status", None) == 429:
                        break
        return summary

    def replay(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            qualified = conn.execute("SELECT token_id FROM research_qualifications WHERE status='VERIFIED'").fetchall()
            bounds = conn.execute("SELECT MIN(open_time),MAX(open_time) FROM ohlcv_bars WHERE timeframe='1h'").fetchone()
        run_id = self.db.create_strategy_run(config_hash(self.config), self.config, "historical", bounds[0], bounds[1])
        trade_count = 0
        for row in qualified:
            with self.db.connect() as conn:
                bars = [dict(item) for item in conn.execute(
                    "SELECT * FROM ohlcv_bars WHERE token_id=? AND timeframe='1h' AND is_closed=1 ORDER BY open_time",
                    (row["token_id"],),
                )]
            signals = replay_bars(bars, self.config)
            self.db.replace_strategy_signals(run_id, row["token_id"], signals)
            trade_count += len(signals)
        self.db.finish_strategy_run(run_id)
        return {"run_id": run_id, "signals": trade_count, "tokens": len(qualified)}
