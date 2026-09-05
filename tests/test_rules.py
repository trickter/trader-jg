from datetime import datetime, timezone

from radar.rules import evaluate


CONFIG = {
    "market_data_max_age_seconds": 180,
    "risk_data_max_age_seconds": 600,
    "rules": {
        "min_liquidity_usd": {"mode": "reject", "value": 50000},
        "min_volume_1h_usd": {"mode": "reject", "value": 10000},
        "min_transactions_1h": {"mode": "reject", "value": 50},
        "max_okx_risk_level": {"mode": "reject", "value": 3, "required": False},
    },
}


def snapshot(**updates):
    values = {"observed_at": "2026-09-05T00:00:00+00:00", "liquidity_usd": 50000, "volume_h1_usd": 10000, "tx_h1": 50}
    values.update(updates)
    return values


def test_threshold_boundaries_pass_and_optional_risk_can_be_missing():
    result = evaluate(snapshot(), CONFIG, "bsc", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "PASS"
    assert result["risk_coverage"] == "missing"


def test_any_valid_hard_failure_rejects_even_when_another_field_missing():
    result = evaluate(snapshot(liquidity_usd=49999, volume_h1_usd=None), CONFIG, "bsc", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "REJECT"
    assert "LOW_LIQUIDITY" in result["reasons"]


def test_missing_core_data_is_unknown_and_empty_is_not_zero():
    result = evaluate(snapshot(tx_h1=None), CONFIG, "solana", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "UNKNOWN"
    assert "MISSING_TX_H1" in result["reasons"]


def test_stale_market_data_is_unknown():
    result = evaluate(snapshot(), CONFIG, "bsc", datetime(2026, 9, 5, 0, 10, tzinfo=timezone.utc))
    assert result["status"] == "UNKNOWN"


def test_required_risk_missing_is_unknown():
    config = {**CONFIG, "rules": {**CONFIG["rules"], "max_okx_risk_level": {"mode": "reject", "value": 3, "required": True}}}
    result = evaluate(snapshot(), config, "bsc", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "UNKNOWN"


def test_warn_mode_does_not_turn_missing_data_into_unknown():
    config = {**CONFIG, "rules": {**CONFIG["rules"], "min_transactions_1h": {"mode": "warn", "value": 50}}}
    result = evaluate(snapshot(tx_h1=None), config, "bsc", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "PASS"


def test_chain_override_merges_only_changed_fields():
    config = {**CONFIG, "chain_overrides": {"bsc": {"min_liquidity_usd": {"value": 75000}}}}
    result = evaluate(snapshot(liquidity_usd=60000), config, "bsc", datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc))
    assert result["status"] == "REJECT"
    assert "LOW_LIQUIDITY" in result["reasons"]
