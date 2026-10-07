from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.modules.research.taiwan_historical_research import evaluate_dataset


FIXTURE = Path(__file__).parent / "fixtures" / "pw13" / "synthetic_snapshot.json"


def _dataset():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _eval_signal(result):
    return next(row for row in result["signals"] if row["split"] == "evaluation")


def test_synthetic_fixture_runs_exact_pw06_conditions_and_purges_cross_split_training_outcome():
    result = evaluate_dataset(_dataset())

    assert result["status"] == "insufficient_evidence"
    assert result["evidence_kind"] == "synthetic"
    assert result["efficacy_claim_allowed"] is False
    assert result["counts"]["factor_values_selected_by_condition"] == {
        "pe_max": 2,
        "pb_max": 2,
        "dividend_yield_min_pct": 2,
        "revenue_yoy_min_pct": 2,
        "institutional_net_min_shares": 2,
    }
    assert result["counts"]["train_samples_after_purge"] == 0
    assert result["counts"]["evaluation_samples"] == 1
    assert result["counts"]["purged_training_signals"] == 1
    assert result["signals"][0]["purge_reason"] == "training_forward_outcome_crosses_evaluation_split"

    outcome = _eval_signal(result)["outcome"]
    assert outcome["status"] == "available"
    assert outcome["entry_date"] == "2026-01-04"
    assert outcome["exit_date"] == "2026-01-06"
    assert outcome["entry_rule"] == "first_retained_stock_observation_strictly_after_decision_date"
    assert outcome["stock_gross_return_pct"] == "1.96078431372549019607843137254901960784313725500"
    assert outcome["stock_cost_assumption_pct"] == "0.1"
    assert outcome["benchmark_id"] == "TAIEX"


def test_decision_timestamp_must_be_aware_and_same_day_close_is_never_an_entry():
    dataset = _dataset()
    dataset["signals"][1]["decision_at_utc"] = "2026-01-03T08:00:00"
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_dataset(dataset)


def test_latest_admissible_missing_value_does_not_fall_back_to_older_favorable_period():
    dataset = _dataset()
    dataset["factor_observations"].append({
        "instrument_id": "TWSE:1234", "condition": "pe_max", "data_period": "2025-10-31",
        "value": "10", "source_contract": "synthetic/valuation/v1", "source_dataset": "valuation",
        "local_snapshot_at_utc": "2025-11-01T06:00:00Z", "revision": 1, "capture_id": "older",
        "payload_sha256": "older-hash",
    })
    latest = next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max")
    latest["value"] = None

    result = evaluate_dataset(dataset)
    signal = _eval_signal(result)
    assert signal["selected"] is False
    assert signal["conditions"]["pe_max"]["reason"] == "latest_admissible_value_missing"
    assert signal["conditions"]["pe_max"]["provenance"]["data_period"] == "2025-12-31"


def test_future_revision_and_late_receipt_cannot_be_backdated_to_decision():
    dataset = _dataset()
    dataset["factor_observations"].append({
        "instrument_id": "TWSE:1234", "condition": "pe_max", "data_period": "2025-12-31",
        "value": "999", "unit": "ratio", "source_contract": "synthetic/valuation/v1", "source_dataset": "valuation",
        "local_snapshot_at_utc": "2026-01-03T07:30:00Z", "source_received_at_utc": "2026-01-03T08:30:00Z",
        "revision": 2, "capture_id": "late-revision", "payload_sha256": "late-revision-hash",
    })
    result = evaluate_dataset(dataset)
    signal = _eval_signal(result)
    assert signal["conditions"]["pe_max"]["value"] == "20.00"

    revised = next(row for row in dataset["factor_observations"] if row.get("capture_id") == "late-revision")
    revised["source_received_at_utc"] = "2026-01-03T07:45:00Z"
    result = evaluate_dataset(dataset)
    assert _eval_signal(result)["conditions"]["pe_max"]["value"] == "999"


def test_available_at_boundary_compares_aware_taipei_and_utc_instants_exactly():
    dataset = _dataset()
    revision = copy.deepcopy(next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max"))
    revision.update({
        "value": "24", "revision": 2, "capture_id": "boundary-revision",
        "payload_sha256": "boundary-hash",
        "local_snapshot_at_utc": "2026-01-03T16:00:00+08:00",
        "source_received_at_utc": "2026-01-03T08:00:00Z",
    })
    dataset["factor_observations"].append(revision)
    assert _eval_signal(evaluate_dataset(dataset))["conditions"]["pe_max"]["value"] == "24"

    revision["source_received_at_utc"] = "2026-01-03T08:00:00.000001Z"
    assert _eval_signal(evaluate_dataset(dataset))["conditions"]["pe_max"]["value"] == "20.00"


def test_conflicting_versions_at_same_period_and_available_time_are_rejected():
    dataset = _dataset()
    conflict = copy.deepcopy(next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max"))
    conflict.update({"value": "29", "capture_id": "conflict", "payload_sha256": "different-hash"})
    dataset["factor_observations"].append(conflict)

    result = evaluate_dataset(dataset)
    signal = _eval_signal(result)
    assert signal["selected"] is False
    assert signal["conditions"]["pe_max"]["reason"] == "ambiguous_same_period_versions"


def test_financial_document_first_observed_cannot_replace_semantic_revision_clock():
    dataset = _dataset()
    pe = next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max")
    pe.update({
        "source_dataset": "financial_statements",
        "document_first_observed_at_utc": "2026-01-01T06:00:00Z",
    })
    pe.pop("local_snapshot_at_utc")
    pe.pop("semantic_revision_first_observed_at_utc", None)
    result = evaluate_dataset(dataset)
    assert _eval_signal(result)["conditions"]["pe_max"]["reason"] == "semantic_revision_observation_missing"


def test_missing_exact_venue_benchmark_date_excludes_outcome_instead_of_using_nearest_date():
    dataset = _dataset()
    dataset["benchmark_bars"] = [
        row for row in dataset["benchmark_bars"]
        if row["trade_date"] != "2026-01-04"
    ]
    result = evaluate_dataset(dataset)
    outcome = _eval_signal(result)["outcome"]
    assert outcome["status"] == "unavailable"
    assert outcome["reason"] == "venue_benchmark_date_missing"
    assert outcome["missing_benchmark_dates"] == ["2026-01-04"]


def test_venue_mismatch_never_uses_the_other_market_benchmark():
    dataset = _dataset()
    for row in dataset["benchmark_bars"]:
        row["benchmark_id"] = "TPEX"
    result = evaluate_dataset(dataset)
    outcome = _eval_signal(result)["outcome"]
    assert outcome["status"] == "unavailable"
    assert outcome["reason"] == "venue_benchmark_date_missing"
    assert outcome["required_benchmark_id"] == "TAIEX"


def test_input_and_evaluation_hashes_are_stable_under_unordered_rows_and_bind_policy():
    dataset = _dataset()
    first = evaluate_dataset(dataset)
    reordered = copy.deepcopy(dataset)
    reordered["factor_observations"].reverse()
    reordered["stock_bars"].reverse()
    reordered["benchmark_bars"].reverse()
    reordered["signals"].reverse()
    second = evaluate_dataset(reordered)
    assert second["dataset_sha256"] == first["dataset_sha256"]
    assert second["evaluation_sha256"] == first["evaluation_sha256"]
    assert second["report_sha256"] == first["report_sha256"]

    changed = copy.deepcopy(dataset)
    changed["evaluation_policy"]["outcome_horizon_sessions"] = 1
    third = evaluate_dataset(changed)
    assert third["dataset_sha256"] != first["dataset_sha256"]
    assert third["evaluation_sha256"] != first["evaluation_sha256"]


def test_latest_revenue_period_is_not_eligible_before_that_month_ends():
    dataset = _dataset()
    revenue = next(row for row in dataset["factor_observations"] if row["condition"] == "revenue_yoy_min_pct")
    revenue["data_period"] = "2026-01"
    result = evaluate_dataset(dataset)
    assert _eval_signal(result)["conditions"]["revenue_yoy_min_pct"]["reason"] == "future_period"


def test_missing_source_version_identity_is_not_eligible():
    dataset = _dataset()
    pe = next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max")
    pe.pop("payload_sha256")
    pe.pop("capture_id")
    pe.pop("revision")
    result = evaluate_dataset(dataset)
    assert _eval_signal(result)["conditions"]["pe_max"]["reason"] == "content_identity_missing"


def test_condition_value_with_wrong_source_unit_is_not_eligible():
    dataset = _dataset()
    pe = next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max")
    pe["unit"] = "percent"
    result = evaluate_dataset(dataset)
    assert _eval_signal(result)["conditions"]["pe_max"]["reason"] == "unit_mismatch"


def test_binary_float_inputs_are_rejected_to_preserve_exact_decimal_semantics():
    dataset = _dataset()
    dataset["conditions"]["pe_max"] = 30.1
    with pytest.raises(ValueError, match="exact decimal strings or integers"):
        evaluate_dataset(dataset)

    dataset = _dataset()
    pe = next(row for row in dataset["factor_observations"] if row["condition"] == "pe_max")
    pe["value"] = 20.1
    with pytest.raises(ValueError, match="exact decimal strings or integers"):
        evaluate_dataset(dataset)


def test_equivalent_decision_instants_cannot_inflate_sample_counts():
    dataset = _dataset()
    dataset["signals"].append({"instrument_id": "TWSE:1234", "decision_at_utc": "2026-01-03T16:00:00+08:00"})
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_dataset(dataset)


@pytest.mark.parametrize("field,value,reason", [("unit", "percent", "unit_mismatch"), ("source_contract", None, "source_contract_missing")])
def test_invalid_latest_known_row_never_falls_back_to_older_valid_data(field, value, reason):
    dataset = _dataset()
    newer = copy.deepcopy(dataset["factor_observations"][0])
    newer.update(data_period="2026-01-02", local_snapshot_at_utc="2026-01-03T07:00:00Z", source_received_at_utc="2026-01-03T07:00:00Z")
    newer[field] = value
    dataset["factor_observations"].append(newer)
    result = _eval_signal(evaluate_dataset(dataset))
    assert result["selected"] is False
    assert result["conditions"]["pe_max"]["reason"] == reason


@pytest.mark.parametrize("condition,period", [("pe_max", "2025-12-26"), ("revenue_yoy_min_pct", "2025-09")])
def test_pw06_daily_and_month_end_freshness_limits_are_preserved(condition, period):
    dataset = _dataset()
    next(row for row in dataset["factor_observations"] if row["condition"] == condition)["data_period"] = period
    assert _eval_signal(evaluate_dataset(dataset))["conditions"][condition]["reason"] == "stale"


@pytest.mark.parametrize("value", [True, "2", "2.5"])
def test_horizon_does_not_silently_coerce_noninteger_policy(value):
    dataset = _dataset()
    dataset["evaluation_policy"]["outcome_horizon_sessions"] = value
    with pytest.raises(ValueError, match="integers"):
        evaluate_dataset(dataset)


def test_institutional_threshold_preserves_pw06_integer_contract():
    dataset = _dataset()
    dataset["conditions"]["institutional_net_min_shares"] = "0"
    with pytest.raises(ValueError, match="must be an integer"):
        evaluate_dataset(dataset)


def test_latest_only_profile_universe_is_excluded_even_with_quality_flag():
    dataset = _dataset()
    dataset["scope"]["universe_kind"] = "latest_profile"
    result = evaluate_dataset(dataset)
    assert result["counts"]["evaluation_samples"] == 0
    assert result["exclusions"]["historical_universe_latest_only"] == 2
    assert result["quality_gates"]["point_in_time_universe"] is False


def test_missing_or_invalid_stock_date_does_not_shift_forward_horizon():
    dataset = _dataset()
    dataset["stock_bars"][2]["close"] = None
    outcome = _eval_signal(evaluate_dataset(dataset))["outcome"]
    assert outcome["status"] == "unavailable"
    assert outcome["reason"] == "stock_window_invalid"
    assert "2026-01-04" in outcome["stock_data_errors"]


def test_purged_evaluation_rows_are_not_counted_as_training_purges():
    dataset = _dataset()
    dataset["evaluation_policy"]["eval_end_date"] = "2026-01-05"
    result = evaluate_dataset(dataset)
    assert result["counts"]["purged_training_signals"] == 1
    assert result["counts"]["purged_evaluation_signals"] == 1


def test_decision_window_and_row_count_are_bounded():
    dataset = _dataset()
    dataset["scope"]["decision_window"]["end_date"] = "2027-01-02"
    with pytest.raises(ValueError, match="366 days"):
        evaluate_dataset(dataset)
    dataset = _dataset()
    dataset["signals"] *= 4_000
    with pytest.raises(ValueError, match="at most 7320"):
        evaluate_dataset(dataset)


def test_multiple_decisions_sharing_the_same_outcome_do_not_inflate_sample_floor():
    dataset = _dataset()
    dataset["signals"].append({"instrument_id": "TWSE:1234", "decision_at_utc": "2026-01-03T09:00:00Z"})
    result = evaluate_dataset(dataset)
    assert result["counts"]["evaluation_samples"] == 1
    assert result["exclusions"]["duplicate_forward_outcome"] == 1


@pytest.mark.parametrize("clock", [None, "2026-01-03T08:00:00Z"])
def test_preregistration_requires_a_clock_before_the_evaluation_boundary(clock):
    dataset = _dataset()
    dataset["evaluation_policy"]["preregistered_at_utc"] = clock
    result = evaluate_dataset(dataset)
    assert result["quality_gates"]["thresholds_preregistered"] is False
    assert result["decision_policy"]["thresholds_preregistered"] is False


@pytest.mark.parametrize("series,field,value", [("stock_bars", "adjustment_mode", "total_return"), ("benchmark_bars", "unit", "TWD/share")])
def test_price_bases_and_units_cannot_be_silently_mixed(series, field, value):
    dataset = _dataset()
    for row in dataset[series]:
        row[field] = value
    assert _eval_signal(evaluate_dataset(dataset))["outcome"]["status"] == "unavailable"


def test_financial_report_received_in_2026_cannot_inform_a_2024_decision():
    dataset = _dataset()
    dataset["scope"]["decision_window"] = {"start_date": "2024-07-01", "end_date": "2024-07-09"}
    dataset["evaluation_policy"].update(train_start_date="2024-07-01", train_end_date="2024-07-02", eval_start_date="2024-07-03", eval_end_date="2024-07-09")
    dataset["signals"] = [{"instrument_id": "TWSE:1234", "decision_at_utc": "2024-07-03T08:00:00Z"}]
    pe = dataset["factor_observations"][0]
    pe.update(data_period="2024-06-30", source_dataset="financial_statements", semantic_revision_first_observed_at_utc="2026-10-04T13:07:32Z", source_received_at_utc="2026-10-04T13:07:32Z", document_first_observed_at_utc="2024-07-01T00:00:00Z")
    result = evaluate_dataset(dataset)
    assert result["signals"][0]["conditions"]["pe_max"]["reason"] == "future_revision_or_receipt"
    assert result["counts"]["evaluation_samples"] == 0
