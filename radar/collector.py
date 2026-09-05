from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from .adapters import (
    BINANCE_CHAINS,
    GMGN_CHAINS,
    OKX_CHAINS,
    BinanceAdapter,
    DexScreenerAdapter,
    GmgnAdapter,
    OkxAdapter,
    SourceError,
    integer,
    millis_to_iso,
    normalize_pair,
    number,
    primary_pair,
)
from .config import Settings, load_rules
from .db import Database, iso, utcnow
from .rules import evaluate


log = logging.getLogger(__name__)


class Collector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.db.initialize()
        self.backoff_until: dict[str, datetime] = {}

    async def _discovery_call(
        self,
        source: str,
        timeframe: str | None,
        call: Callable[[], Awaitable[tuple[list[dict[str, Any]], dict[str, Any], Any]]],
    ) -> None:
        if source in self.backoff_until and utcnow() < self.backoff_until[source]:
            return
        run_id = self.db.start_run(source, "discovery")
        try:
            items, params, raw = await call()
            self.db.save_raw(run_id, source, raw)
            observed = iso()
            for item in items:
                token_id = self.db.upsert_token(item["chain"], item["address"], item.get("symbol"), item.get("name"), item.get("logo"), observed)
                self.db.record_listing(token_id, source, timeframe, item["rank"], observed, run_id, params)
                if source.startswith("okx"):
                    self.db.save_aggregate(token_id, "okx", timeframe or "", observed, item.get("metrics") or {})
                elif source.startswith("gmgn"):
                    self.db.save_aggregate(token_id, "gmgn", timeframe or "", observed, item.get("metrics") or {})
            self.db.finish_run(run_id, "success_empty" if not items else "success", len(items))
        except SourceError as exc:
            self.db.finish_run(run_id, "failed", message=str(exc), http_status=exc.status, error_code=exc.code)
            if exc.code in ("RATE_LIMITED", "PAYMENT_REQUIRED"):
                seconds = 900 if exc.code == "PAYMENT_REQUIRED" else 120
                self.backoff_until[source] = datetime.fromtimestamp(utcnow().timestamp() + seconds, timezone.utc)
            log.warning("%s discovery failed: %s", source, exc)
        except Exception as exc:
            self.db.finish_run(run_id, "failed", message=f"unexpected {exc.__class__.__name__}", error_code="UNEXPECTED")
            log.exception("%s discovery failed", source)

    async def discover(self) -> None:
        timeout = httpx.Timeout(15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            binance = BinanceAdapter(client)
            okx = OkxAdapter(client, self.settings)
            gmgn = GmgnAdapter(client, self.settings)
            jobs = []
            for chain in BINANCE_CHAINS:
                jobs.append(self._discovery_call(f"binance_alpha_{chain}", "1h", lambda chain=chain: binance.alpha(chain)))
            for chain in OKX_CHAINS:
                jobs.append(self._discovery_call(f"okx_trending_{chain}_4h", "4h", lambda chain=chain: okx.trending(chain, "4h")))
            for chain in GMGN_CHAINS:
                jobs.append(self._discovery_call(f"gmgn_trending_{chain}_6h", "6h", lambda chain=chain: gmgn.trending(chain, "6h")))
            await asyncio.gather(*jobs)

    @staticmethod
    def _merge_okx(snapshot: dict[str, Any], aggregate: tuple[dict[str, Any], str] | None) -> None:
        if not aggregate:
            return
        data, observed = aggregate
        snapshot.update({
            "okx_first_trade_at": millis_to_iso(data.get("firstTradeTime")),
            "okx_unique_traders_h1": integer(data.get("uniqueTraders")),
            "okx_net_inflow_h1": number(data.get("inflowUsd")),
            "okx_top10_percent": number(data.get("top10HoldPercent")),
            "okx_dev_percent": number(data.get("devHoldPercent")),
            "okx_insider_percent": number(data.get("insiderHoldPercent")),
            "okx_bundle_percent": number(data.get("bundleHoldPercent")),
            "okx_risk_level": integer(data.get("riskLevelControl")),
            "risk_observed_at": observed,
        })

    @staticmethod
    def _merge_gmgn(snapshot: dict[str, Any], aggregate: tuple[dict[str, Any], str] | None) -> None:
        if not aggregate:
            return
        data, _ = aggregate
        used_fallback = False
        for target, source in (
            ("price_usd", "price"),
            ("market_cap_usd", "market_cap"),
            ("liquidity_usd", "liquidity"),
            ("volume_h6_usd", "volume"),
            ("tx_h6", "swaps"),
        ):
            if snapshot.get(target) is None and (value := number(data.get(source))) is not None:
                snapshot[target] = integer(value) if target == "tx_h6" else value
                used_fallback = True
        if used_fallback:
            snapshot["data_source"] = "dexscreener+gmgn" if snapshot.get("pair_address") else "gmgn"

    async def market(self) -> None:
        config, config_hash = load_rules(self.settings.rules_path)
        rule_version = self.db.rule_version(config_hash, config)
        tokens = self.db.active_tokens(int(config.get("observation_hours", 72)))
        timeout = httpx.Timeout(15)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            dex = DexScreenerAdapter(client)
            for chain in ("bsc", "solana", "robinhood"):
                chain_tokens = [row for row in tokens if row["chain"] == chain]
                for offset in range(0, len(chain_tokens), 30):
                    batch = chain_tokens[offset : offset + 30]
                    run_id = self.db.start_run(f"dex_market_{chain}", "market")
                    try:
                        pair_map, raw = await dex.pairs_for_tokens(chain, [row["address"] for row in batch])
                        self.db.save_raw(run_id, f"dex_market_{chain}", raw)
                        observed = iso()
                        for token in batch:
                            pair = primary_pair(pair_map.get(token["address"], []))
                            if pair:
                                base = pair.get("baseToken") or {}
                                self.db.update_token_metadata(token["id"], base.get("symbol"), base.get("name"), (pair.get("info") or {}).get("imageUrl"))
                            snapshot = normalize_pair(pair, observed)
                            self._merge_gmgn(snapshot, self.db.latest_aggregate(token["id"], "gmgn", "6h"))
                            self._merge_okx(snapshot, self.db.latest_aggregate(token["id"], "okx", "4h"))
                            snapshot_id = self.db.save_snapshot(token["id"], snapshot)
                            result = evaluate(snapshot, config, token["chain"])
                            self.db.save_evaluation(token["id"], snapshot_id, rule_version, result)
                        self.db.finish_run(run_id, "success_empty" if not raw else "success", len(batch))
                    except SourceError as exc:
                        self.db.finish_run(run_id, "failed", message=str(exc), http_status=exc.status, error_code=exc.code)
                        log.warning("market collection failed for %s: %s", chain, exc)
                    except Exception as exc:
                        self.db.finish_run(run_id, "failed", message=f"unexpected {exc.__class__.__name__}", error_code="UNEXPECTED")
                        log.exception("market collection failed for %s", chain)

    async def once(self) -> None:
        await self.discover()
        await self.market()

    async def run_forever(self) -> None:
        next_discovery = 0.0
        next_market = 0.0
        next_cleanup = 0.0
        while True:
            config, _ = load_rules(self.settings.rules_path)
            loop_time = asyncio.get_running_loop().time()
            if loop_time >= next_discovery:
                await self.discover()
                next_discovery = loop_time + int(config.get("discovery_interval_seconds", 300))
            if loop_time >= next_market:
                await self.market()
                next_market = loop_time + int(config.get("market_interval_seconds", 60))
            if loop_time >= next_cleanup:
                self.db.cleanup(int(config.get("raw_retention_days", 7)), int(config.get("snapshot_retention_days", 90)))
                next_cleanup = loop_time + 86400
            await asyncio.sleep(max(1, min(next_discovery, next_market, next_cleanup) - asyncio.get_running_loop().time()))
