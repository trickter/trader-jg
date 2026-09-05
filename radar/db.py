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

    def active_tokens(self, observation_hours: int) -> list[sqlite3.Row]:
        cutoff = iso(utcnow() - timedelta(hours=observation_hours))
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM tokens WHERE last_seen_at>=? ORDER BY last_seen_at DESC", (cutoff,)))

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
