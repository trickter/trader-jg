from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tokens (
  id INTEGER PRIMARY KEY,
  chain TEXT NOT NULL,
  address TEXT NOT NULL,
  address_key TEXT NOT NULL,
  symbol TEXT,
  name TEXT,
  logo_url TEXT,
  first_discovered_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  UNIQUE(chain, address_key)
);
CREATE INDEX IF NOT EXISTS idx_tokens_last_seen ON tokens(last_seen_at);

CREATE TABLE IF NOT EXISTS listing_records (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  source TEXT NOT NULL,
  timeframe TEXT,
  rank INTEGER,
  observed_at TEXT NOT NULL,
  run_id INTEGER,
  upstream_params_json TEXT NOT NULL,
  UNIQUE(token_id, source, timeframe, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_listings_token_source ON listing_records(token_id, source);

CREATE TABLE IF NOT EXISTS aggregate_metrics (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  source TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  observed_at TEXT NOT NULL,
  pair_address TEXT,
  dex_id TEXT,
  quote_symbol TEXT,
  price_usd REAL,
  liquidity_usd REAL,
  market_cap_usd REAL,
  fdv_usd REAL,
  volume_m5_usd REAL,
  volume_h1_usd REAL,
  volume_h6_usd REAL,
  tx_m5 INTEGER,
  tx_h1 INTEGER,
  tx_h6 INTEGER,
  buys_h1 INTEGER,
  sells_h1 INTEGER,
  pair_created_at TEXT,
  okx_first_trade_at TEXT,
  okx_unique_traders_h1 INTEGER,
  okx_net_inflow_h1 REAL,
  okx_top10_percent REAL,
  okx_dev_percent REAL,
  okx_insider_percent REAL,
  okx_bundle_percent REAL,
  okx_risk_level INTEGER,
  risk_observed_at TEXT,
  data_source TEXT NOT NULL DEFAULT 'dexscreener'
);
CREATE INDEX IF NOT EXISTS idx_snapshots_token_time ON market_snapshots(token_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS rule_versions (
  id INTEGER PRIMARY KEY,
  config_hash TEXT NOT NULL UNIQUE,
  loaded_at TEXT NOT NULL,
  config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  snapshot_id INTEGER NOT NULL REFERENCES market_snapshots(id),
  rule_version_id INTEGER NOT NULL REFERENCES rule_versions(id),
  evaluated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_coverage TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  checks_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_eval_snapshot ON evaluations(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_eval_token_time ON evaluations(token_id, evaluated_at DESC);

CREATE TABLE IF NOT EXISTS pair_switches (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  observed_at TEXT NOT NULL,
  from_pair TEXT,
  to_pair TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0,
  http_status INTEGER,
  error_code TEXT,
  message TEXT,
  request_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raw_responses (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized_metrics (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  source TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  field TEXT NOT NULL,
  value REAL,
  UNIQUE(token_id, source, timeframe, observed_at, field)
);
CREATE INDEX IF NOT EXISTS idx_metrics_token_time ON normalized_metrics(token_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS research_qualifications (
  token_id INTEGER PRIMARY KEY REFERENCES tokens(id),
  status TEXT NOT NULL CHECK(status IN ('VERIFIED','PENDING','EXCLUDED')),
  peak_market_cap_usd REAL,
  evidence_at TEXT,
  known_at TEXT NOT NULL,
  evidence_source TEXT,
  reason TEXT,
  implied_supply_ratio REAL
);
CREATE INDEX IF NOT EXISTS idx_qualifications_status ON research_qualifications(status);

CREATE TABLE IF NOT EXISTS ohlcv_bars (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  source TEXT NOT NULL,
  chain TEXT NOT NULL,
  pair_address TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  open_time TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  is_closed INTEGER NOT NULL DEFAULT 1,
  fetched_at TEXT NOT NULL,
  UNIQUE(source, pair_address, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_token_time ON ohlcv_bars(token_id, timeframe, open_time);

CREATE TABLE IF NOT EXISTS backfill_jobs (
  id INTEGER PRIMARY KEY,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  source TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  cursor_before INTEGER,
  status TEXT NOT NULL,
  next_retry_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(token_id, source, timeframe, start_at, end_at)
);

CREATE TABLE IF NOT EXISTS strategy_runs (
  id INTEGER PRIMARY KEY,
  config_hash TEXT NOT NULL,
  config_json TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('historical','forward')),
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  data_start TEXT,
  data_end TEXT
);

CREATE TABLE IF NOT EXISTS strategy_signals (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
  token_id INTEGER NOT NULL REFERENCES tokens(id),
  high_time TEXT NOT NULL,
  high_price REAL NOT NULL,
  level_ratio REAL NOT NULL,
  level_price REAL NOT NULL,
  triggered_at TEXT NOT NULL,
  entry_type TEXT NOT NULL,
  entry_time TEXT,
  entry_price REAL,
  exit_time TEXT,
  exit_price REAL,
  outcome TEXT NOT NULL,
  net_return REAL,
  ambiguous INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  UNIQUE(run_id, token_id, high_time, level_ratio, entry_type)
);
CREATE INDEX IF NOT EXISTS idx_signals_run_outcome ON strategy_signals(run_id, outcome);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES(1,?)", (iso(),))

    def upsert_token(self, chain: str, address: str, symbol: str | None, name: str | None, logo: str | None, seen_at: str) -> int:
        key = address if chain == "solana" else address.lower()
        with self._lock, self.connect() as conn:
            conn.execute(
                """INSERT INTO tokens(chain,address,address_key,symbol,name,logo_url,first_discovered_at,last_seen_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chain,address_key) DO UPDATE SET
                symbol=COALESCE(excluded.symbol,tokens.symbol), name=COALESCE(excluded.name,tokens.name),
                logo_url=COALESCE(excluded.logo_url,tokens.logo_url), last_seen_at=excluded.last_seen_at""",
                (chain, address, key, symbol, name, logo, seen_at, seen_at),
            )
            return int(conn.execute("SELECT id FROM tokens WHERE chain=? AND address_key=?", (chain, key)).fetchone()[0])

    def update_token_metadata(self, token_id: int, symbol: str | None, name: str | None, logo: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE tokens SET symbol=COALESCE(symbol,?),name=COALESCE(name,?),logo_url=COALESCE(logo_url,?) WHERE id=?",
                (symbol, name, logo, token_id),
            )

    def start_run(self, source: str, kind: str) -> int:
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO collection_runs(source,kind,started_at,status) VALUES(?,?,?,'running')", (source, kind, iso()))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, count: int = 0, message: str | None = None, http_status: int | None = None, error_code: str | None = None, request_count: int = 1) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE collection_runs SET finished_at=?,status=?,item_count=?,message=?,http_status=?,error_code=?,request_count=? WHERE id=?", (iso(), status, count, message, http_status, error_code, request_count, run_id))

    def save_raw(self, run_id: int, source: str, payload: Any) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO raw_responses(run_id,source,fetched_at,payload_json) VALUES(?,?,?,?)", (run_id, source, iso(), json.dumps(payload, ensure_ascii=False)))

    def record_listing(self, token_id: int, source: str, timeframe: str | None, rank: int, observed_at: str, run_id: int, params: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO listing_records(token_id,source,timeframe,rank,observed_at,run_id,upstream_params_json) VALUES(?,?,?,?,?,?,?)", (token_id, source, timeframe, rank, observed_at, run_id, json.dumps(params, sort_keys=True)))

    def save_aggregate(self, token_id: int, source: str, timeframe: str, observed_at: str, data: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO aggregate_metrics(token_id,source,timeframe,observed_at,data_json) VALUES(?,?,?,?,?)", (token_id, source, timeframe, observed_at, json.dumps(data)))

    def save_normalized_metrics(self, token_id: int, source: str, timeframe: str, observed_at: str, values: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO normalized_metrics(token_id,source,timeframe,observed_at,field,value) VALUES(?,?,?,?,?,?)",
                [(token_id, source, timeframe, observed_at, field, value) for field, value in values.items() if value is not None],
            )

    def active_tokens(self, observation_hours: int) -> list[sqlite3.Row]:
        cutoff = iso(utcnow() - timedelta(hours=observation_hours))
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM tokens WHERE last_seen_at>=? ORDER BY last_seen_at DESC", (cutoff,)))

    def tracked_tokens(self, observation_hours: int) -> list[sqlite3.Row]:
        """Return every token that still requires live pricing."""
        cutoff = iso(utcnow() - timedelta(hours=observation_hours))
        with self.connect() as conn:
            return list(conn.execute(
                """SELECT DISTINCT t.* FROM tokens t
                LEFT JOIN research_qualifications q ON q.token_id=t.id AND q.status='VERIFIED'
                LEFT JOIN strategy_signals s ON s.token_id=t.id AND s.entry_time IS NOT NULL
                  AND s.outcome IN ('OPEN','PENDING')
                WHERE t.last_seen_at>=? OR q.token_id IS NOT NULL OR s.id IS NOT NULL
                ORDER BY t.last_seen_at DESC""",
                (cutoff,),
            ))

    def upsert_qualification(self, token_id: int, status: str, peak_market_cap_usd: float | None,
                             evidence_at: str | None, evidence_source: str | None, reason: str | None,
                             implied_supply_ratio: float | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO research_qualifications(token_id,status,peak_market_cap_usd,evidence_at,known_at,evidence_source,reason,implied_supply_ratio)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(token_id) DO UPDATE SET status=excluded.status,
                peak_market_cap_usd=excluded.peak_market_cap_usd,evidence_at=excluded.evidence_at,
                known_at=excluded.known_at,evidence_source=excluded.evidence_source,reason=excluded.reason,
                implied_supply_ratio=excluded.implied_supply_ratio""",
                (token_id, status, peak_market_cap_usd, evidence_at, iso(), evidence_source, reason, implied_supply_ratio),
            )

    def save_ohlcv_bars(self, token_id: int, source: str, chain: str, pair_address: str,
                        timeframe: str, bars: list[dict[str, Any]]) -> int:
        rows = [(
            token_id, source, chain, pair_address, timeframe, bar["open_time"], bar["open"], bar["high"],
            bar["low"], bar["close"], bar["volume"], int(bar.get("is_closed", True)), iso(),
        ) for bar in bars]
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT INTO ohlcv_bars(token_id,source,chain,pair_address,timeframe,open_time,open,high,low,close,volume,is_closed,fetched_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,pair_address,timeframe,open_time) DO UPDATE SET
                token_id=excluded.token_id,open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                volume=excluded.volume,is_closed=excluded.is_closed,fetched_at=excluded.fetched_at""",
                rows,
            )
            return conn.total_changes - before

    def start_backfill_job(self, token_id: int, source: str, timeframe: str,
                           start_at: str, end_at: str, cursor_before: int) -> tuple[int, int]:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO backfill_jobs(token_id,source,timeframe,start_at,end_at,cursor_before,status,attempts,updated_at)
                VALUES(?,?,?,?,?,?,'running',1,?) ON CONFLICT(token_id,source,timeframe,start_at,end_at)
                DO UPDATE SET status='running',next_retry_at=NULL,
                attempts=backfill_jobs.attempts+1,last_error=NULL,updated_at=excluded.updated_at""",
                (token_id, source, timeframe, start_at, end_at, cursor_before, iso()),
            )
            row = conn.execute(
                "SELECT id,cursor_before FROM backfill_jobs WHERE token_id=? AND source=? AND timeframe=? AND start_at=? AND end_at=?",
                (token_id, source, timeframe, start_at, end_at),
            ).fetchone()
            return int(row["id"]), int(row["cursor_before"])

    def update_backfill_job(self, job_id: int, status: str, cursor_before: int | None = None,
                            last_error: str | None = None, retry_after_seconds: int | None = None) -> None:
        next_retry = iso(utcnow() + timedelta(seconds=retry_after_seconds)) if retry_after_seconds else None
        with self.connect() as conn:
            conn.execute(
                """UPDATE backfill_jobs SET status=?,cursor_before=COALESCE(?,cursor_before),
                next_retry_at=?,last_error=?,updated_at=? WHERE id=?""",
                (status, cursor_before, next_retry, last_error, iso(), job_id),
            )

    def create_strategy_run(self, config_hash: str, config: dict[str, Any], mode: str,
                            data_start: str | None = None, data_end: str | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO strategy_runs(config_hash,config_json,mode,status,created_at,data_start,data_end) VALUES(?,?,?,'running',?,?,?)",
                (config_hash, json.dumps(config, sort_keys=True), mode, iso(), data_start, data_end),
            )
            return int(cur.lastrowid)

    def finish_strategy_run(self, run_id: int, status: str = "complete") -> None:
        with self.connect() as conn:
            conn.execute("UPDATE strategy_runs SET status=?,finished_at=? WHERE id=?", (status, iso(), run_id))

    def replace_strategy_signals(self, run_id: int, token_id: int, signals: list[dict[str, Any]]) -> None:
        columns = ("run_id", "token_id", "high_time", "high_price", "level_ratio", "level_price", "triggered_at",
                   "entry_type", "entry_time", "entry_price", "exit_time", "exit_price", "outcome", "net_return",
                   "ambiguous", "note")
        with self.connect() as conn:
            conn.execute("DELETE FROM strategy_signals WHERE run_id=? AND token_id=?", (run_id, token_id))
            conn.executemany(
                f"INSERT INTO strategy_signals({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                [tuple([run_id, token_id] + [signal.get(name) for name in columns[2:]]) for signal in signals],
            )

    def latest_aggregate(self, token_id: int, source: str, timeframe: str) -> tuple[dict[str, Any], str] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT data_json,observed_at FROM aggregate_metrics WHERE token_id=? AND source=? AND timeframe=? ORDER BY observed_at DESC LIMIT 1", (token_id, source, timeframe)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def rule_version(self, config_hash: str, config: dict[str, Any]) -> int:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO rule_versions(config_hash,loaded_at,config_json) VALUES(?,?,?)", (config_hash, iso(), json.dumps(config, sort_keys=True)))
            return int(conn.execute("SELECT id FROM rule_versions WHERE config_hash=?", (config_hash,)).fetchone()[0])

    def save_snapshot(self, token_id: int, values: dict[str, Any]) -> int:
        columns = ["token_id", *values.keys()]
        with self.connect() as conn:
            previous = conn.execute("SELECT pair_address FROM market_snapshots WHERE token_id=? AND pair_address IS NOT NULL ORDER BY observed_at DESC LIMIT 1", (token_id,)).fetchone()
            marks = ",".join("?" for _ in columns)
            cur = conn.execute(f"INSERT INTO market_snapshots({','.join(columns)}) VALUES({marks})", [token_id, *values.values()])
            if values.get("pair_address") and previous and previous[0] != values["pair_address"]:
                conn.execute("INSERT INTO pair_switches(token_id,observed_at,from_pair,to_pair) VALUES(?,?,?,?)", (token_id, values["observed_at"], previous[0], values["pair_address"]))
            return int(cur.lastrowid)

    def save_evaluation(self, token_id: int, snapshot_id: int, rule_version_id: int, result: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO evaluations(token_id,snapshot_id,rule_version_id,evaluated_at,status,risk_coverage,reasons_json,checks_json) VALUES(?,?,?,?,?,?,?,?)", (token_id, snapshot_id, rule_version_id, iso(), result["status"], result["risk_coverage"], json.dumps(result["reasons"]), json.dumps(result["checks"])))

    def cleanup(self, raw_days: int, snapshot_days: int) -> None:
        raw_cutoff = iso(utcnow() - timedelta(days=raw_days))
        snapshot_cutoff = iso(utcnow() - timedelta(days=snapshot_days))
        with self.connect() as conn:
            conn.execute("DELETE FROM raw_responses WHERE fetched_at<?", (raw_cutoff,))
            conn.execute("DELETE FROM evaluations WHERE snapshot_id IN (SELECT id FROM market_snapshots WHERE observed_at<?)", (snapshot_cutoff,))
            conn.execute("DELETE FROM market_snapshots WHERE observed_at<?", (snapshot_cutoff,))
            conn.execute("DELETE FROM aggregate_metrics WHERE observed_at<?", (snapshot_cutoff,))
