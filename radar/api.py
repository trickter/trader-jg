from __future__ import annotations

import csv
import io
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings, load_rules
from .db import Database, iso, utcnow


settings = Settings.from_env()
db = Database(settings.db_path)
db.initialize()
STATIC = ROOT / "radar" / "static"
app = FastAPI(title="热门币雷达", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _candidate_filter_parts(
    view: str,
    chain: str | None,
    status: str | None,
    source: str | None,
    include_reject: bool,
    search: str | None = None,
) -> tuple[str, list[object], str, list[object]]:
    config, _ = load_rules(settings.rules_path)
    now = utcnow()
    current_cutoff = iso(now - timedelta(seconds=int(config.get("discovery_interval_seconds", 300)) * 2))
    archive_cutoff = iso(now - timedelta(hours=int(config.get("observation_hours", 72))))
    clauses: list[str] = []
    where_args: list[object] = []
    state_expr = "CASE WHEN t.last_seen_at>=? THEN 'current' WHEN t.last_seen_at>=? THEN 'watching' ELSE 'archived' END"
    if view == "active":
        clauses.append("t.last_seen_at>=?")
        where_args.append(archive_cutoff)
    elif view != "all":
        clauses.append(f"({state_expr})=?")
        where_args.extend((current_cutoff, archive_cutoff, view))
    if chain:
        clauses.append("t.chain=?")
        where_args.append(chain)
    if status:
        clauses.append("e.status=?")
        where_args.append(status)
    elif not include_reject:
        clauses.append("(e.status IS NULL OR e.status!='REJECT')")
    if source:
        clauses.append("EXISTS(SELECT 1 FROM listing_records lr WHERE lr.token_id=t.id AND lr.source LIKE ?)")
        where_args.append(f"%{source}%")
    if search and (term := search.strip()):
        clauses.append("(t.symbol LIKE ? COLLATE NOCASE OR t.name LIKE ? COLLATE NOCASE OR t.address LIKE ? COLLATE NOCASE)")
        pattern = f"%{term}%"
        where_args.extend((pattern, pattern, pattern))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return state_expr, [current_cutoff, archive_cutoff], where, where_args


def _candidate_sql(
    view: str,
    chain: str | None,
    status: str | None,
    source: str | None,
    include_reject: bool,
    search: str | None = None,
    sort: str | None = None,
) -> tuple[str, list[object]]:
    state_expr, state_args, where, where_args = _candidate_filter_parts(
        view, chain, status, source, include_reject, search
    )
    order = {
        "market_cap_desc": "s.market_cap_usd IS NULL, s.market_cap_usd DESC, t.last_seen_at DESC",
        "market_cap_asc": "s.market_cap_usd IS NULL, s.market_cap_usd ASC, t.last_seen_at DESC",
    }.get(sort, "t.last_seen_at DESC")
    sql = f"""
    SELECT t.*, {state_expr} AS view_state,
      s.id snapshot_id,s.observed_at,s.pair_address,s.dex_id,s.quote_symbol,s.price_usd,s.liquidity_usd,
      s.market_cap_usd,s.fdv_usd,s.volume_h1_usd,s.tx_h1,s.buys_h1,s.sells_h1,s.pair_created_at,
      s.okx_first_trade_at,s.okx_unique_traders_h1,s.okx_net_inflow_h1,s.okx_top10_percent,s.okx_dev_percent,
      s.okx_insider_percent,s.okx_bundle_percent,s.okx_risk_level,
      e.status,e.risk_coverage,e.reasons_json,
      (SELECT group_concat(DISTINCT source) FROM listing_records WHERE token_id=t.id) sources
    FROM tokens t
    LEFT JOIN market_snapshots s ON s.id=(SELECT id FROM market_snapshots WHERE token_id=t.id ORDER BY observed_at DESC LIMIT 1)
    LEFT JOIN evaluations e ON e.id=(SELECT id FROM evaluations WHERE token_id=t.id ORDER BY evaluated_at DESC LIMIT 1)
    {where} ORDER BY {order}
    """
    return sql, [*state_args, *where_args]


def _candidate_status_sql(
    view: str,
    chain: str | None,
    status: str | None,
    source: str | None,
    include_reject: bool,
    search: str | None = None,
) -> tuple[str, list[object]]:
    _, _, where, where_args = _candidate_filter_parts(
        view, chain, status, source, include_reject, search
    )
    sql = f"""
    SELECT COALESCE(e.status, 'UNKNOWN') status, COUNT(*) count
    FROM tokens t
    LEFT JOIN evaluations e ON e.id=(SELECT id FROM evaluations WHERE token_id=t.id ORDER BY evaluated_at DESC LIMIT 1)
    {where}
    GROUP BY COALESCE(e.status, 'UNKNOWN')
    """
    return sql, where_args


def _row(row):
    data = dict(row)
    for key in ("reasons_json", "checks_json", "config_json", "upstream_params_json"):
        if key in data and data[key]:
            data[key.removesuffix("_json")] = json.loads(data.pop(key))
    return data


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/research")
def research_page():
    return FileResponse(STATIC / "research.html")


@app.get("/api/candidates")
def candidates(
    view: Literal["active", "current", "watching", "archived", "all"] = "active",
    chain: Literal["bsc", "solana", "robinhood"] | None = None,
    status: Literal["PASS", "REJECT", "UNKNOWN"] | None = None,
    source: str | None = None,
    search: str | None = Query(None, max_length=200),
    sort: Literal["market_cap_asc", "market_cap_desc"] | None = None,
    include_reject: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    page: int | None = Query(None, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    sql, args = _candidate_sql(view, chain, status, source, include_reject, search, sort)
    with db.connect() as conn:
        if page is not None:
            status_sql, status_args = _candidate_status_sql(view, chain, status, source, include_reject, search)
            status_rows = conn.execute(status_sql, status_args).fetchall()
            status_counts = {"PASS": 0, "UNKNOWN": 0, "REJECT": 0}
            status_counts.update({row["status"]: row["count"] for row in status_rows})
            total = sum(status_counts.values())
            pages = max(1, math.ceil(total / page_size))
            page = min(page, pages)
            rows = conn.execute(sql + " LIMIT ? OFFSET ?", [*args, page_size, (page - 1) * page_size]).fetchall()
            return {
                "items": [_row(row) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": pages,
                "status_counts": status_counts,
            }
        rows = conn.execute(sql + " LIMIT ?", [*args, limit]).fetchall()
    return [_row(row) for row in rows]


@app.get("/api/candidates/{token_id}")
def candidate_detail(token_id: int):
    with db.connect() as conn:
        token = conn.execute("SELECT * FROM tokens WHERE id=?", (token_id,)).fetchone()
        if not token:
            raise HTTPException(404, "token not found")
        listings = conn.execute("SELECT source,timeframe,rank,observed_at,upstream_params_json FROM listing_records WHERE token_id=? ORDER BY observed_at DESC LIMIT 300", (token_id,)).fetchall()
        evaluations = conn.execute("SELECT e.*,rv.config_hash FROM evaluations e JOIN rule_versions rv ON rv.id=e.rule_version_id WHERE e.token_id=? ORDER BY e.evaluated_at DESC LIMIT 300", (token_id,)).fetchall()
        switches = conn.execute("SELECT * FROM pair_switches WHERE token_id=? ORDER BY observed_at DESC", (token_id,)).fetchall()
    return {"token": _row(token), "listings": [_row(row) for row in listings], "evaluations": [_row(row) for row in evaluations], "pair_switches": [_row(row) for row in switches]}


@app.get("/api/candidates/{token_id}/snapshots")
def snapshots(token_id: int, hours: int = Query(72, ge=1, le=2160), limit: int = Query(2000, ge=1, le=10000)):
    cutoff = iso(utcnow() - timedelta(hours=hours))
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM market_snapshots WHERE token_id=? AND observed_at>=? ORDER BY observed_at ASC LIMIT ?", (token_id, cutoff, limit)).fetchall()
    return [_row(row) for row in rows]


@app.get("/api/health/sources")
def source_health():
    with db.connect() as conn:
        latest = conn.execute("SELECT r.* FROM collection_runs r JOIN (SELECT source,MAX(id) id FROM collection_runs GROUP BY source) x ON x.id=r.id ORDER BY source").fetchall()
        totals = conn.execute("SELECT source,COUNT(*) runs,SUM(request_count) requests,SUM(CASE WHEN status LIKE 'success%' THEN 1 ELSE 0 END) successes FROM collection_runs GROUP BY source ORDER BY source").fetchall()
    return {"okx_configured": settings.okx_configured, "latest": [_row(row) for row in latest], "totals": [_row(row) for row in totals]}


@app.get("/api/export.csv")
def export_csv(view: Literal["active", "current", "watching", "archived", "all"] = "active"):
    sql, args = _candidate_sql(view, None, None, None, True)
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, args).fetchall()]
    output = io.StringIO()
    fields = ["id", "chain", "address", "symbol", "view_state", "sources", "status", "risk_coverage", "price_usd", "market_cap_usd", "liquidity_usd", "volume_h1_usd", "tx_h1", "okx_risk_level", "first_discovered_at", "last_seen_at", "observed_at", "reasons_json"]
    writer = csv.DictWriter(output, fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=hot-coin-radar.csv"})


@app.get("/api/config")
def config_info():
    config, config_hash = load_rules(settings.rules_path)
    return {"hash": config_hash, "config": config}


@app.get("/api/report")
def collection_report(days: int = Query(7, ge=1, le=90)):
    cutoff = iso(utcnow() - timedelta(days=days))
    with db.connect() as conn:
        discoveries = conn.execute("SELECT chain,COUNT(DISTINCT id) tokens FROM tokens WHERE first_discovered_at>=? GROUP BY chain", (cutoff,)).fetchall()
        outcomes = conn.execute("SELECT status,COUNT(*) count FROM evaluations WHERE evaluated_at>=? GROUP BY status", (cutoff,)).fetchall()
        reasons = conn.execute("SELECT reasons_json FROM evaluations WHERE evaluated_at>=?", (cutoff,)).fetchall()
        coverage = conn.execute("SELECT risk_coverage,COUNT(*) count FROM evaluations WHERE evaluated_at>=? GROUP BY risk_coverage", (cutoff,)).fetchall()
        sources = conn.execute("SELECT source,COUNT(*) runs,SUM(request_count) requests,SUM(CASE WHEN status LIKE 'success%' THEN 1 ELSE 0 END) successes,SUM(item_count) items FROM collection_runs WHERE started_at>=? GROUP BY source", (cutoff,)).fetchall()
    reason_counts: dict[str, int] = {}
    for row in reasons:
        for reason in json.loads(row[0]):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {"days": days, "discoveries": [_row(row) for row in discoveries], "outcomes": [_row(row) for row in outcomes], "risk_coverage": [_row(row) for row in coverage], "filter_reasons": reason_counts, "sources": [_row(row) for row in sources]}


@app.get("/api/strategy/candidates")
def strategy_candidates(
    chain: Literal["bsc", "solana", "robinhood"] | None = None,
    qualification: Literal["VERIFIED", "PENDING", "EXCLUDED"] | None = None,
    signal: Literal["WIN", "LOSS", "TIMEOUT", "OPEN", "AMBIGUOUS", "UNCONFIRMED"] | None = None,
    search: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
):
    clauses = []
    args: list[object] = []
    if chain:
        clauses.append("t.chain=?")
        args.append(chain)
    if qualification:
        clauses.append("q.status=?")
        args.append(qualification)
    if signal:
        clauses.append("sig.outcome=?")
        args.append(signal)
    if search and search.strip():
        clauses.append("(t.symbol LIKE ? COLLATE NOCASE OR t.name LIKE ? COLLATE NOCASE OR t.address LIKE ? COLLATE NOCASE)")
        pattern = f"%{search.strip()}%"
        args.extend((pattern, pattern, pattern))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    sql = f"""
    WITH latest_run AS (SELECT MAX(id) id FROM strategy_runs WHERE status='complete'),
    ranked_bars AS (
      SELECT token_id,open_time,close,
        ROW_NUMBER() OVER (PARTITION BY token_id ORDER BY open_time DESC) AS recency
      FROM ohlcv_bars WHERE timeframe='1h' AND is_closed=1
    ),
    bars AS (
      SELECT token_id,COUNT(*) bar_count,MIN(open_time) bar_start,MAX(open_time) bar_end,MAX(close) window_high
      FROM ranked_bars WHERE recency<=720 GROUP BY token_id
    )
    SELECT t.id,t.chain,t.address,t.symbol,t.name,t.last_seen_at,
      q.status qualification_status,q.peak_market_cap_usd,q.evidence_at,q.known_at,q.evidence_source,
      q.reason qualification_reason,q.implied_supply_ratio,
      snap.price_usd,snap.market_cap_usd,snap.liquidity_usd,snap.volume_h1_usd,snap.observed_at,
      bars.bar_count,bars.bar_start,bars.bar_end,bars.window_high,
      sig.id signal_id,sig.level_ratio,sig.level_price,sig.triggered_at,sig.entry_type,sig.entry_price,
      sig.outcome signal_outcome,sig.net_return,sig.ambiguous
    FROM tokens t
    JOIN research_qualifications q ON q.token_id=t.id
    LEFT JOIN market_snapshots snap ON snap.id=(SELECT id FROM market_snapshots WHERE token_id=t.id ORDER BY observed_at DESC LIMIT 1)
    LEFT JOIN bars ON bars.token_id=t.id
    LEFT JOIN strategy_signals sig ON sig.id=(
      SELECT id FROM strategy_signals WHERE token_id=t.id AND run_id=(SELECT id FROM latest_run)
      ORDER BY triggered_at DESC,id DESC LIMIT 1
    )
    {where}
    ORDER BY CASE q.status WHEN 'VERIFIED' THEN 0 WHEN 'PENDING' THEN 1 ELSE 2 END,
      CASE WHEN sig.outcome='OPEN' THEN 0 WHEN sig.entry_time IS NOT NULL THEN 1 WHEN sig.id IS NOT NULL THEN 2 ELSE 3 END,
      snap.liquidity_usd DESC,t.last_seen_at DESC LIMIT ?
    """
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(sql, [*args, limit]).fetchall()]
    levels = (2 / 3, 1 / 3, 1 / 6)
    for row in rows:
        high = row.get("window_high")
        current = row.get("price_usd")
        row["price_to_high"] = current / high if current and high else None
        row["levels"] = {str(round(level, 6)): high * level for level in levels} if high else {}
    return {"items": rows, "total": len(rows)}


@app.get("/api/strategy/candidates/{token_id}")
def strategy_candidate_detail(token_id: int):
    with db.connect() as conn:
        token = conn.execute("SELECT * FROM tokens WHERE id=?", (token_id,)).fetchone()
        if not token:
            raise HTTPException(404, "token not found")
        qualification_row = conn.execute("SELECT * FROM research_qualifications WHERE token_id=?", (token_id,)).fetchone()
        signals = conn.execute(
            """SELECT s.*,r.config_hash,r.mode,r.created_at run_created_at FROM strategy_signals s
            JOIN strategy_runs r ON r.id=s.run_id WHERE s.token_id=? ORDER BY s.triggered_at DESC LIMIT 300""",
            (token_id,),
        ).fetchall()
        coverage = conn.execute(
            "SELECT COUNT(*) count,MIN(open_time) start,MAX(open_time) end FROM ohlcv_bars WHERE token_id=? AND timeframe='1h'",
            (token_id,),
        ).fetchone()
    return {
        "token": _row(token),
        "qualification": _row(qualification_row) if qualification_row else None,
        "coverage": _row(coverage),
        "signals": [_row(row) for row in signals],
    }


@app.get("/api/strategy/candidates/{token_id}/candles")
def strategy_candles(token_id: int, limit: int = Query(1000, ge=1, le=5000), before: str | None = None):
    clauses = ["token_id=?", "timeframe='1h'"]
    args: list[object] = [token_id]
    if before:
        clauses.append("open_time<?")
        args.append(before)
    with db.connect() as conn:
        exists = conn.execute("SELECT 1 FROM tokens WHERE id=?", (token_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "token not found")
        rows = conn.execute(
            f"SELECT * FROM ohlcv_bars WHERE {' AND '.join(clauses)} ORDER BY open_time DESC LIMIT ?",
            [*args, limit],
        ).fetchall()
    return list(reversed([_row(row) for row in rows]))


@app.get("/api/strategy/runs/{run_id}/report")
def strategy_run_report(run_id: int):
    with db.connect() as conn:
        run = conn.execute("SELECT * FROM strategy_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "strategy run not found")
        groups = conn.execute(
            """SELECT entry_type,level_ratio,outcome,COUNT(*) count,AVG(net_return) average_return,
            COUNT(DISTINCT token_id) tokens FROM strategy_signals WHERE run_id=?
            GROUP BY entry_type,level_ratio,outcome ORDER BY entry_type,level_ratio DESC,outcome""",
            (run_id,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM strategy_signals WHERE run_id=?", (run_id,)).fetchone()[0]
        decided = conn.execute(
            """SELECT COUNT(*) count,
            SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) wins,
            AVG(CASE WHEN outcome IN ('WIN','LOSS','TIMEOUT') THEN net_return END) average_return
            FROM strategy_signals WHERE run_id=? AND outcome IN ('WIN','LOSS','TIMEOUT')""",
            (run_id,),
        ).fetchone()
    summary = _row(decided)
    summary["win_rate"] = summary["wins"] / summary["count"] if summary["count"] else None
    return {"run": _row(run), "total": total, "summary": summary, "groups": [_row(row) for row in groups]}


@app.get("/api/strategy/health")
def strategy_health():
    with db.connect() as conn:
        qualifications = conn.execute("SELECT status,COUNT(*) count FROM research_qualifications GROUP BY status").fetchall()
        bars = conn.execute("SELECT chain,COUNT(*) bars,COUNT(DISTINCT token_id) tokens,MAX(open_time) latest FROM ohlcv_bars GROUP BY chain").fetchall()
        jobs = conn.execute(
            """SELECT j.status,COUNT(*) count FROM backfill_jobs j
            JOIN (SELECT token_id,source,timeframe,MAX(id) id FROM backfill_jobs GROUP BY token_id,source,timeframe) latest
              ON latest.id=j.id GROUP BY j.status"""
        ).fetchall()
        latest_run = conn.execute("SELECT * FROM strategy_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "qualifications": [_row(row) for row in qualifications],
        "ohlcv": [_row(row) for row in bars],
        "backfill_jobs": [_row(row) for row in jobs],
        "latest_run": _row(latest_run) if latest_run else None,
    }
