from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


RULE_FIELDS = {
    "min_liquidity_usd": ("liquidity_usd", "min", "LOW_LIQUIDITY"),
    "min_volume_1h_usd": ("volume_h1_usd", "min", "LOW_VOLUME_1H"),
    "min_transactions_1h": ("tx_h1", "min", "LOW_TRANSACTIONS_1H"),
    "max_okx_risk_level": ("okx_risk_level", "max", "HIGH_OKX_RISK"),
}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate(snapshot: dict[str, Any], config: dict[str, Any], chain: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    definitions = {name: dict(value) for name, value in (config.get("rules") or {}).items()}
    for name, override in ((config.get("chain_overrides") or {}).get(chain, {})).items():
        definitions[name] = {**definitions.get(name, {}), **override}
    checks: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    missing_required: list[str] = []
    warnings: list[str] = []

    market_time = parse_time(snapshot.get("observed_at"))
    market_fresh = bool(market_time and (now - market_time).total_seconds() <= int(config.get("market_data_max_age_seconds", 180)))
    risk_time = parse_time(snapshot.get("risk_observed_at"))
    risk_fresh = bool(risk_time and (now - risk_time).total_seconds() <= int(config.get("risk_data_max_age_seconds", 600)))

    for rule_name, (field, direction, reason) in RULE_FIELDS.items():
        rule = definitions.get(rule_name) or {"mode": "disabled"}
        mode = rule.get("mode", "disabled")
        if mode == "disabled":
            continue
        value = snapshot.get(field)
        fresh = risk_fresh if field == "okx_risk_level" else market_fresh
        missing = value is None or not fresh or (field == "okx_risk_level" and value == 0)
        threshold = rule.get("value")
        failed = False if missing else (value < threshold if direction == "min" else value > threshold)
        check = {"rule": rule_name, "field": field, "mode": mode, "actual": value, "threshold": threshold, "fresh": fresh, "outcome": "missing" if missing else "failed" if failed else "passed", "reason": reason}
        checks.append(check)
        if missing:
            if mode == "reject" and (field != "okx_risk_level" or rule.get("required", False)):
                missing_required.append(f"MISSING_{field.upper()}")
        elif failed and mode == "reject":
            hard_failures.append(reason)
        elif failed and mode == "warn":
            warnings.append(reason)

    if hard_failures:
        status = "REJECT"
    elif missing_required:
        status = "UNKNOWN"
    else:
        status = "PASS"
    risk_fields = ("okx_risk_level", "okx_top10_percent", "okx_dev_percent", "okx_insider_percent", "okx_bundle_percent")
    present = sum(
        snapshot.get(name) is not None and not (name == "okx_risk_level" and snapshot.get(name) == 0)
        for name in risk_fields
    )
    coverage = "complete" if present == len(risk_fields) and risk_fresh else "partial" if present and risk_fresh else "missing"
    return {"status": status, "risk_coverage": coverage, "reasons": hard_failures + missing_required + warnings, "checks": checks}
