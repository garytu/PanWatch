"""Deterministic point-in-time screening research for bounded Taiwan snapshots.

This module is deliberately offline. It accepts an immutable, provenance-bearing
snapshot and never calls a provider, loads a current universe, or changes strategy
configuration. See the PW-13 runbook for the input contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping
from zoneinfo import ZoneInfo


_TAIPEI = ZoneInfo("Asia/Taipei")
_ID = re.compile(r"^(TWSE|TPEX):([0-9][0-9A-Z]{3,5})$")
_BENCHMARK_FOR_VENUE = {"TWSE": "TAIEX", "TPEX": "TPEX"}
_ROW_LIMITS = {"factor_observations": 40_000, "signals": 7_320, "stock_bars": 20_000, "benchmark_bars": 2_000}
_CONDITION_RULES = {
    "pe_max": ("<=", "ratio", Decimal("0"), Decimal("1000")),
    "pb_max": ("<=", "ratio", Decimal("0"), Decimal("1000")),
    "dividend_yield_min_pct": (">=", "percent", Decimal("0"), Decimal("1000")),
    "revenue_yoy_min_pct": (">=", "percent", Decimal("-1000"), Decimal("100000")),
    "institutional_net_min_shares": (">=", "shares", Decimal("-10000000000"), Decimal("10000000000")),
}


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        raise ValueError("numeric inputs must use exact decimal strings or integers, not binary floats")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _aware(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware ISO-8601 timestamp")
    return parsed.astimezone(timezone.utc)


def _period_end(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = (int(part) for part in value.split("-"))
            if not 1 <= month <= 12:
                return None
            if month == 12:
                return date(year + 1, 1, 1) - timedelta(days=1)
            return date(year, month + 1, 1) - timedelta(days=1)
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.isoformat() == value else None


def canonical_sha256(value: object) -> str:
    """Hash JSON-compatible data with stable key ordering and exact strings."""
    def normalize(item: object) -> object:
        if isinstance(item, dict):
            return {key: normalize(item[key]) for key in sorted(item)}
        if isinstance(item, list):
            normalized = [normalize(part) for part in item]
            return sorted(normalized, key=lambda part: json.dumps(part, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return item

    encoded = json.dumps(
        normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _condition_map(raw: object) -> dict[str, Decimal]:
    if not isinstance(raw, dict):
        raise ValueError("conditions must be an object")
    result: dict[str, Decimal] = {}
    for name, value in raw.items():
        if name not in _CONDITION_RULES:
            raise ValueError(f"unsupported PW-06 condition: {name}")
        if name == "institutional_net_min_shares" and type(value) is not int:
            raise ValueError("institutional_net_min_shares must be an integer")
        number = _decimal(value)
        operator, _unit, lower, upper = _CONDITION_RULES[name]
        if number is None or (name in {"pe_max", "pb_max"} and not lower < number <= upper):
            raise ValueError(f"invalid threshold for {name}")
        if name not in {"pe_max", "pb_max"} and not lower <= number <= upper:
            raise ValueError(f"invalid threshold for {name}")
        if name == "institutional_net_min_shares" and number != number.to_integral_value():
            raise ValueError("institutional_net_min_shares must be an integer")
        result[name] = number
    return result


def _factor_available_at(factor: Mapping[str, Any]) -> tuple[datetime | None, str | None]:
    """Return conservative availability for this exact retained content version.

    A financial statement's document-first-observed clock is intentionally not a
    fallback for its semantic revision clock. A later receipt can establish when
    a retained version became knowable, but never makes it available earlier.
    """
    source_dataset = factor.get("source_dataset")
    if source_dataset == "financial_statements":
        raw_first = factor.get("semantic_revision_first_observed_at_utc")
        if raw_first is None:
            raw_first = factor.get("local_snapshot_at_utc")
            if raw_first is None:
                return None, "semantic_revision_observation_missing"
    elif "semantic_revision_first_observed_at_utc" in factor:
        raw_first = factor.get("semantic_revision_first_observed_at_utc") or factor.get("local_snapshot_at_utc")
        if raw_first is None:
            return None, "semantic_revision_observation_missing"
    else:
        raw_first = factor.get("local_snapshot_at_utc")
        if raw_first is None and factor.get("observation_time_semantics") == "exact_content_version":
            raw_first = factor.get("content_first_observed_at_utc") or factor.get("first_observed_at_utc")
        if raw_first is None and factor.get("receipt_binds_content_version") is True:
            raw_first = factor.get("source_received_at_utc") or factor.get("received_at_utc")
    if raw_first is None:
        return None, "observation_time_missing"
    try:
        first = _aware(raw_first, "factor observation time")
        clocks = [first]
        receipt = factor.get("source_received_at_utc") or factor.get("received_at_utc") or factor.get("acquired_at_utc")
        if receipt is not None:
            clocks.append(_aware(receipt, "factor receipt time"))
        if factor.get("publication_time_verified") is True:
            published = factor.get("published_at_utc")
            if published is None:
                return None, "verified_publication_time_missing"
            clocks.append(_aware(published, "verified publisher time"))
        return max(clocks), None
    except ValueError:
        return None, "invalid_observation_time"


def _factor_fingerprint(factor: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(factor))


def _factor_provenance(factor: Mapping[str, Any], available_at: datetime | None) -> dict[str, Any]:
    return {
        "data_period": factor.get("data_period"),
        "unit": factor.get("unit"),
        "available_at_utc": (
            available_at.isoformat().replace("+00:00", "Z") if available_at is not None else None
        ),
        "source_contract": factor.get("source_contract"),
        "source_dataset": factor.get("source_dataset"),
        "source_received_at_utc": factor.get("source_received_at_utc") or factor.get("received_at_utc"),
        "local_snapshot_at_utc": factor.get("local_snapshot_at_utc"),
        "semantic_revision_first_observed_at_utc": factor.get("semantic_revision_first_observed_at_utc"),
        "revision": factor.get("revision"),
        "capture_id": factor.get("capture_id"),
        "payload_sha256": factor.get("payload_sha256"),
        "snapshot_sha256": factor.get("snapshot_sha256"),
    }


def _bar_provenance_error(row: Mapping[str, Any]) -> str | None:
    if not (row.get("source_contract") or row.get("dataset")):
        return "bar_source_contract_missing"
    if not any(row.get(key) for key in ("payload_sha256", "capture_id", "revision", "dataset_sha256")):
        return "bar_content_identity_missing"
    raw_time = (
        row.get("source_received_at_utc") or row.get("captured_at_utc")
        or row.get("acquired_at_utc") or row.get("local_snapshot_at_utc")
    )
    if raw_time is None:
        return "bar_receipt_time_missing"
    try:
        _aware(raw_time, "bar receipt time")
    except ValueError:
        return "bar_receipt_time_invalid"
    return None


def _resolve_factor(
    observations: list[dict[str, Any]],
    *,
    instrument_id: str,
    condition: str,
    decision_at: datetime,
) -> dict[str, Any]:
    candidates: list[tuple[date, datetime, dict[str, Any], Decimal | None]] = []
    reasons: list[str] = []
    rejected: list[tuple[date | None, datetime | None, dict[str, Any], str]] = []
    local_decision_date = decision_at.astimezone(_TAIPEI).date()
    saw_factor = False
    for row in observations:
        if row.get("instrument_id") != instrument_id or row.get("condition") != condition:
            continue
        saw_factor = True
        period_end = _period_end(row.get("data_period"))
        if period_end is None:
            reasons.append("invalid_data_period")
            rejected.append((None, None, row, "invalid_data_period"))
            continue
        if period_end > local_decision_date:
            reasons.append("future_period")
            rejected.append((period_end, None, row, "future_period"))
            continue
        available_at, reason = _factor_available_at(row)
        if reason is not None or available_at is None:
            rejection_reason = reason or "observation_time_missing"
            reasons.append(rejection_reason)
            rejected.append((period_end, None, row, rejection_reason))
            continue
        if available_at > decision_at:
            reasons.append("future_revision_or_receipt")
            rejected.append((period_end, available_at, row, "future_revision_or_receipt"))
            continue
        # Select the latest known period/version before checking its value or
        # source validity. A malformed latest row must not resurrect an older one.
        candidates.append((period_end, available_at, row, None))

    if not candidates:
        if not saw_factor:
            reason = "condition_observation_missing"
        else:
            priority = (
                "semantic_revision_observation_missing", "future_revision_or_receipt",
                "future_period", "observation_time_missing", "unit_mismatch", "source_contract_missing",
                "content_identity_missing", "invalid_data_period", "invalid_observation_time",
            )
            reason = next((item for item in priority if item in reasons), "no_admissible_observation")
        matching_rejections = [item for item in rejected if item[3] == reason]
        if not matching_rejections:
            matching_rejections = rejected
        if matching_rejections:
            period_end, available_at, row, _rejection_reason = max(
                matching_rejections,
                key=lambda item: (item[0] or date.min, item[1] or datetime.min.replace(tzinfo=timezone.utc), _factor_fingerprint(item[2])),
            )
            return {
                "eligible": False,
                "reason": reason,
                "data_period": period_end.isoformat() if period_end else row.get("data_period"),
                "provenance": _factor_provenance(row, available_at),
            }
        return {"eligible": False, "reason": reason}

    latest_period = max(item[0] for item in candidates)
    same_period = [item for item in candidates if item[0] == latest_period]
    latest_available = max(item[1] for item in same_period)
    latest_versions = [item for item in same_period if item[1] == latest_available]
    fingerprints = {_factor_fingerprint(item[2]) for item in latest_versions}
    if len(fingerprints) > 1:
        return {"eligible": False, "reason": "ambiguous_same_period_versions", "data_period": latest_period.isoformat()}
    selected = min(latest_versions, key=lambda item: _factor_fingerprint(item[2]))
    _period, available_at, row, value = selected
    provenance = _factor_provenance(row, available_at)
    reason = None
    if row.get("unit") != _CONDITION_RULES[condition][1]:
        reason = "unit_mismatch"
    elif not isinstance(row.get("source_contract"), str) or not row["source_contract"]:
        reason = "source_contract_missing"
    elif not any(row.get(key) for key in ("payload_sha256", "content_hash", "capture_id", "snapshot_sha256", "revision")):
        reason = "content_identity_missing"
    monthly = condition == "revenue_yoy_min_pct"
    period_pattern = r"\d{4}-\d{2}" if monthly else r"\d{4}-\d{2}-\d{2}"
    if reason is None and not re.fullmatch(period_pattern, row["data_period"]):
        reason = "invalid_condition_period"
    if reason is None and (local_decision_date - latest_period).days > (90 if monthly else 7):
        reason = "stale"
    if reason:
        return {"eligible": False, "reason": reason, "provenance": provenance}
    value = _decimal(row.get("value"))
    if condition == "institutional_net_min_shares" and value is not None and value != value.to_integral_value():
        return {"eligible": False, "reason": "noninteger_share_count", "provenance": provenance}
    if value is None:
        return {"eligible": False, "reason": "latest_admissible_value_missing", "provenance": provenance}
    return {"eligible": True, "value": value, "provenance": provenance}


def _clean_bars(dataset: Mapping[str, Any], instrument_id: str) -> tuple[list[tuple[date, Decimal]], dict[str, str]]:
    match = _ID.fullmatch(instrument_id)
    if match is None:
        return [], {"invalid_identity": instrument_id}
    venue = match.group(1)
    selected: dict[date, tuple[Decimal, str]] = {}
    errors: dict[str, str] = {}
    conflicting_dates: set[date] = set()
    for row in dataset.get("stock_bars", []):
        if row.get("instrument_id") != instrument_id:
            continue
        try:
            trade_date = date.fromisoformat(row.get("trade_date", ""))
        except (TypeError, ValueError):
            errors[str(row.get("trade_date"))] = "invalid_stock_date"
            continue
        if row.get("venue") != venue:
            errors[trade_date.isoformat()] = "stock_venue_mismatch"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        if row.get("unit") != "TWD/share" or row.get("adjustment_mode") != "raw":
            errors[trade_date.isoformat()] = "stock_price_basis_or_unit_mismatch"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        price = _decimal(row.get("close"))
        if price is None or price <= 0:
            errors[trade_date.isoformat()] = "invalid_stock_close"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        provenance_error = _bar_provenance_error(row)
        if provenance_error is not None:
            errors[trade_date.isoformat()] = provenance_error
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        if trade_date in conflicting_dates:
            continue
        fingerprint = canonical_sha256(row)
        if trade_date in selected and selected[trade_date] != (price, fingerprint):
            errors[trade_date.isoformat()] = "conflicting_stock_bars"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        selected[trade_date] = (price, fingerprint)
    return sorted((trade_date, item[0]) for trade_date, item in selected.items()), errors


def _clean_benchmarks(dataset: Mapping[str, Any], venue: str) -> tuple[dict[date, Decimal], dict[str, str]]:
    expected = _BENCHMARK_FOR_VENUE[venue]
    selected: dict[date, tuple[Decimal, str]] = {}
    errors: dict[str, str] = {}
    conflicting_dates: set[date] = set()
    for row in dataset.get("benchmark_bars", []):
        if row.get("venue") != venue and row.get("benchmark_id") != expected:
            continue
        if row.get("venue") != venue or row.get("benchmark_id") != expected:
            raw_day = row.get("trade_date")
            errors[str(raw_day)] = "venue_benchmark_mismatch"
            try:
                mismatched_day = date.fromisoformat(raw_day)
            except (TypeError, ValueError):
                continue
            selected.pop(mismatched_day, None)
            conflicting_dates.add(mismatched_day)
            continue
        try:
            trade_date = date.fromisoformat(row.get("trade_date", ""))
        except (TypeError, ValueError):
            errors[str(row.get("trade_date"))] = "invalid_benchmark_date"
            continue
        if row.get("unit") != "index_points" or row.get("basis") != "raw_price_index":
            errors[trade_date.isoformat()] = "benchmark_price_basis_or_unit_mismatch"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        price = _decimal(row.get("close"))
        if price is None or price <= 0:
            errors[trade_date.isoformat()] = "invalid_benchmark_close"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        provenance_error = _bar_provenance_error(row)
        if provenance_error is not None:
            errors[trade_date.isoformat()] = provenance_error
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        if trade_date in conflicting_dates:
            continue
        fingerprint = canonical_sha256(row)
        if trade_date in selected and selected[trade_date] != (price, fingerprint):
            errors[trade_date.isoformat()] = "conflicting_benchmark_bars"
            selected.pop(trade_date, None)
            conflicting_dates.add(trade_date)
            continue
        selected[trade_date] = (price, fingerprint)
    return {trade_date: item[0] for trade_date, item in selected.items()}, errors


def _forward_outcome(
    dataset: Mapping[str, Any],
    instrument_id: str,
    decision_at: datetime,
    horizon_sessions: int,
    cost_policy: Mapping[str, Any],
) -> dict[str, Any]:
    venue = _ID.fullmatch(instrument_id).group(1)
    stock, stock_errors = _clean_bars(dataset, instrument_id)
    decision_date = decision_at.astimezone(_TAIPEI).date()
    future = [(day, price) for day, price in stock if day > decision_date]
    if len(future) <= horizon_sessions:
        return {
            "status": "unavailable", "reason": "stock_horizon_missing",
            "entry_date": future[0][0].isoformat() if future else None,
            "available_stock_observation_count": len(future),
            "horizon_sessions": horizon_sessions,
            "stock_data_errors": stock_errors,
        }
    entry_date, entry_price = future[0]
    exit_date, exit_price = future[horizon_sessions]
    invalid_window = {day: reason for day, reason in stock_errors.items() if decision_date.isoformat() < day <= exit_date.isoformat()}
    if invalid_window:
        return {"status": "unavailable", "reason": "stock_window_invalid", "stock_data_errors": invalid_window}
    benchmark, benchmark_errors = _clean_benchmarks(dataset, venue)
    missing_dates = [d.isoformat() for d in (entry_date, exit_date) if d not in benchmark]
    if missing_dates:
        return {
            "status": "unavailable", "reason": "venue_benchmark_date_missing",
            "entry_date": entry_date.isoformat(), "exit_date": exit_date.isoformat(),
            "missing_benchmark_dates": missing_dates,
            "required_benchmark_id": _BENCHMARK_FOR_VENUE[venue],
            "benchmark_data_errors": benchmark_errors,
        }
    entry_cost_bps = _decimal(cost_policy.get("entry_cost_bps"))
    exit_cost_bps = _decimal(cost_policy.get("exit_cost_bps"))
    if entry_cost_bps is None or exit_cost_bps is None or min(entry_cost_bps, exit_cost_bps) < 0:
        return {"status": "unavailable", "reason": "invalid_explicit_cost_policy"}
    with localcontext() as context:
        context.prec = 48
        stock_gross_pct = (exit_price / entry_price - Decimal(1)) * Decimal(100)
        benchmark_gross_pct = (benchmark[exit_date] / benchmark[entry_date] - Decimal(1)) * Decimal(100)
        stock_cost_pct = (entry_cost_bps + exit_cost_bps) / Decimal(100)
        stock_net_pct = stock_gross_pct - stock_cost_pct
        relative_net_pp = stock_net_pct - benchmark_gross_pct
    return {
        "status": "available",
        "basis": "raw_price_return",
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "entry_rule": "first_retained_stock_observation_strictly_after_decision_date",
        "exit_rule": "horizon_sessions_after_entry_in_retained_observation_sequence",
        "stock_entry_close": str(entry_price),
        "stock_exit_close": str(exit_price),
        "benchmark_id": _BENCHMARK_FOR_VENUE[venue],
        "benchmark_entry_close": str(benchmark[entry_date]),
        "benchmark_exit_close": str(benchmark[exit_date]),
        "stock_gross_return_pct": str(stock_gross_pct),
        "stock_cost_assumption_pct": str(stock_cost_pct),
        "stock_net_return_pct": str(stock_net_pct),
        "benchmark_gross_return_pct": str(benchmark_gross_pct),
        "relative_net_percentage_points": str(relative_net_pp),
        "cost_policy": dict(cost_policy),
        "stock_data_errors": stock_errors,
        "benchmark_data_errors": benchmark_errors,
    }


def evaluate_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a pre-bounded dataset without fetching or modifying any data.

    Conditions use the exact PW-06 operators and source units. Every signal is
    evaluated against the most recent admissible period/version available by its
    timezone-aware decision timestamp. Training outcomes crossing the evaluation
    boundary are purged.
    """
    if dataset.get("schema_version") != "panwatch.pw13.snapshot.v1":
        raise ValueError("unsupported PW-13 dataset schema_version")
    for name, limit in _ROW_LIMITS.items():
        rows = dataset.get(name, [])
        if not isinstance(rows, list) or len(rows) > limit or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"{name} must be an array of objects with at most {limit} rows")
    conditions = _condition_map(dataset.get("conditions"))
    audit_conditions = dataset.get("availability_audit_conditions", [])
    if not isinstance(audit_conditions, list) or any(name not in _CONDITION_RULES for name in audit_conditions):
        raise ValueError("availability_audit_conditions must contain supported PW-06 condition names")
    if len(set(audit_conditions)) != len(audit_conditions):
        raise ValueError("availability_audit_conditions must not contain duplicates")
    input_hash = canonical_sha256(dataset)
    policy = dataset.get("evaluation_policy")
    if not isinstance(policy, dict):
        raise ValueError("evaluation_policy must be an object")
    try:
        train_start = date.fromisoformat(policy["train_start_date"])
        train_end = date.fromisoformat(policy["train_end_date"])
        eval_start = date.fromisoformat(policy["eval_start_date"])
        eval_end = date.fromisoformat(policy["eval_end_date"])
        horizon = policy["outcome_horizon_sessions"]
        min_eval = policy["minimum_eval_samples"]
        min_train = policy["minimum_train_samples"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("evaluation_policy has invalid date or integer fields") from exc
    if not (train_start <= train_end < eval_start <= eval_end):
        raise ValueError("train/evaluation dates must be ordered with a strict split")
    if not all(type(value) is int for value in (horizon, min_eval, min_train)):
        raise ValueError("horizon and minimum sample counts must be integers")
    if not 1 <= horizon <= 250 or min_eval < 1 or min_train < 0:
        raise ValueError("horizon and minimum sample counts are out of range")
    cost_policy = policy.get("cost_policy")
    if not isinstance(cost_policy, dict) or cost_policy.get("unit") != "basis_points_per_side":
        raise ValueError("explicit cost_policy in basis_points_per_side is required")
    costs = [_decimal(cost_policy.get(name)) for name in ("entry_cost_bps", "exit_cost_bps")]
    if any(value is None or not 0 <= value <= 10_000 for value in costs):
        raise ValueError("explicit cost assumptions must be between 0 and 10000 basis points")

    observations = dataset.get("factor_observations", [])
    signals = dataset.get("signals", [])
    if not isinstance(observations, list) or not isinstance(signals, list):
        raise ValueError("factor_observations and signals must be arrays")
    scope = dataset.get("scope")
    if not isinstance(scope, dict) or not isinstance(scope.get("instrument_ids"), list):
        raise ValueError("scope.instrument_ids must be an explicit array")
    quality = dataset.get("quality", {})
    if not isinstance(quality, dict):
        raise ValueError("quality must be an object")
    scope_ids = scope["instrument_ids"]
    if not 1 <= len(scope_ids) <= 20 or not all(isinstance(item, str) and _ID.fullmatch(item) for item in scope_ids):
        raise ValueError("scope must contain only canonical TWSE:/TPEX: IDs")
    selected_scope = sorted(set(scope_ids))
    universe_kind = scope.get("universe_kind", "fixed_selectors")
    if universe_kind not in {"fixed_selectors", "point_in_time_snapshot", "latest_profile"}:
        raise ValueError("unsupported universe_kind")
    try:
        window = scope["decision_window"]
        window_start = date.fromisoformat(window["start_date"])
        window_end = date.fromisoformat(window["end_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("scope.decision_window requires inclusive dates") from exc
    if not (window_start <= train_start <= eval_end <= window_end) or (window_end - window_start).days >= 366:
        raise ValueError("declared splits must fit a decision window of at most 366 days")
    ordered_signals = sorted(
        signals,
        key=lambda item: (str(item.get("decision_at_utc")), str(item.get("instrument_id")), canonical_sha256(item)),
    )
    signal_keys = [(item.get("instrument_id"), _aware(item.get("decision_at_utc"), "decision_at_utc")) for item in ordered_signals]
    if len(signal_keys) != len(set(signal_keys)):
        raise ValueError("duplicate instrument/decision timestamp signals are ambiguous")
    signal_results: list[dict[str, Any]] = []
    availability_audit: list[dict[str, Any]] = []
    train: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    sample_keys: set[tuple[str, str, str, str]] = set()
    exclusions: dict[str, int] = {}
    factor_selection_counts = {name: 0 for name in conditions}

    for signal in ordered_signals:
        instrument_id = signal.get("instrument_id")
        identity = _ID.fullmatch(instrument_id) if isinstance(instrument_id, str) else None
        if identity is None or instrument_id not in selected_scope:
            exclusions["out_of_scope_or_invalid_identity"] = exclusions.get("out_of_scope_or_invalid_identity", 0) + 1
            continue
        decision_at = _aware(signal.get("decision_at_utc"), "decision_at_utc")
        decision_date = decision_at.astimezone(_TAIPEI).date()
        if universe_kind == "latest_profile":
            exclusions["historical_universe_latest_only"] = exclusions.get("historical_universe_latest_only", 0) + 1
            signal_results.append({"instrument_id": instrument_id, "decision_at_utc": decision_at.isoformat(), "selected": False, "reason": "historical_universe_latest_only"})
            continue
        if train_start <= decision_date <= train_end:
            split = "train"
        elif eval_start <= decision_date <= eval_end:
            split = "evaluation"
        else:
            exclusions["outside_declared_splits"] = exclusions.get("outside_declared_splits", 0) + 1
            continue
        factor_results: dict[str, Any] = {}
        if audit_conditions:
            audit_factors: dict[str, Any] = {}
            for condition in audit_conditions:
                resolved_audit = _resolve_factor(
                    observations, instrument_id=instrument_id, condition=condition,
                    decision_at=decision_at,
                )
                if resolved_audit.get("eligible"):
                    audit_factors[condition] = {
                        "status": "available_by_decision",
                        "value": str(resolved_audit["value"]),
                        "data_period": resolved_audit["provenance"].get("data_period"),
                        "provenance": resolved_audit.get("provenance"),
                    }
                else:
                    audit_factors[condition] = {
                        "status": "unavailable_by_decision",
                        "reason": resolved_audit.get("reason"),
                        "data_period": (
                            resolved_audit.get("data_period")
                            or (resolved_audit.get("provenance") or {}).get("data_period")
                        ),
                        "provenance": resolved_audit.get("provenance"),
                    }
            availability_audit.append({
                "instrument_id": instrument_id,
                "decision_at_utc": decision_at.isoformat().replace("+00:00", "Z"),
                "decision_date_taipei": decision_date.isoformat(),
                "split": split,
                "conditions": audit_factors,
            })
        passed_conditions = True
        for condition, threshold in conditions.items():
            resolved = _resolve_factor(
                observations, instrument_id=instrument_id, condition=condition,
                decision_at=decision_at,
            )
            if not resolved.get("eligible"):
                factor_results[condition] = {
                    "passed": False, "reason": resolved.get("reason"),
                    "data_period": resolved.get("data_period"),
                    "provenance": resolved.get("provenance"),
                }
                passed_conditions = False
                continue
            factor_selection_counts[condition] += 1
            value: Decimal = resolved["value"]
            operator = _CONDITION_RULES[condition][0]
            passed = value <= threshold if operator == "<=" else value >= threshold
            factor_results[condition] = {
                "value": str(value), "operator": operator,
                "threshold": str(threshold), "unit": _CONDITION_RULES[condition][1],
                "passed": passed,
                "reason": "matched" if passed else "condition_not_met",
                "provenance": resolved.get("provenance"),
            }
            passed_conditions = passed_conditions and passed
        result: dict[str, Any] = {
            "instrument_id": instrument_id,
            "venue": identity.group(1),
            "decision_at_utc": decision_at.isoformat().replace("+00:00", "Z"),
            "decision_date_taipei": decision_date.isoformat(),
            "split": split,
            "conditions": factor_results,
            "selected": (passed_conditions if conditions else None),
        }
        if result["selected"]:
            result["outcome"] = _forward_outcome(dataset, instrument_id, decision_at, horizon, cost_policy)
            if result["outcome"]["status"] != "available":
                exclusions[result["outcome"].get("reason", "outcome_unavailable")] = exclusions.get(result["outcome"].get("reason", "outcome_unavailable"), 0) + 1
            if split == "train" and result["outcome"].get("status") == "available" and date.fromisoformat(result["outcome"]["exit_date"]) >= eval_start:
                result["purged"] = True
                result["purge_reason"] = "training_forward_outcome_crosses_evaluation_split"
            elif split == "evaluation" and result["outcome"].get("status") == "available" and date.fromisoformat(result["outcome"]["exit_date"]) > eval_end:
                result["purged"] = True
                result["purge_reason"] = "evaluation_forward_outcome_exceeds_evaluation_window"
                exclusions["evaluation_forward_outcome_exceeds_evaluation_window"] = exclusions.get("evaluation_forward_outcome_exceeds_evaluation_window", 0) + 1
            elif result["outcome"].get("status") == "available":
                sample_key = (split, instrument_id, result["outcome"]["entry_date"], result["outcome"]["exit_date"])
                if sample_key in sample_keys:
                    result["sample_exclusion_reason"] = "duplicate_forward_outcome"
                    exclusions["duplicate_forward_outcome"] = exclusions.get("duplicate_forward_outcome", 0) + 1
                else:
                    sample_keys.add(sample_key)
                    (train if split == "train" else evaluation).append(result)
        else:
            for details in factor_results.values():
                reason = details.get("reason")
                if reason not in {"condition_not_met", "matched"}:
                    exclusions[reason or "factor_ineligible"] = exclusions.get(reason or "factor_ineligible", 0) + 1
        signal_results.append(result)

    train_count = len(train)
    eval_count = len(evaluation)
    quality_gates = {
        "real_evidence": dataset.get("evidence_kind") in {"official_captured_snapshot", "official_retained_evidence_summary"},
        "thresholds_preregistered": bool(conditions) and policy.get("thresholds_preregistered") is True,
        "point_in_time_universe": universe_kind == "point_in_time_snapshot" and quality.get("point_in_time_universe_verified") is True,
        "calendar_verified": quality.get("exchange_calendar_verified") is True,
        "corporate_action_adjusted_or_complete": quality.get("corporate_actions_complete") is True,
        "market_cost_model_supported": cost_policy.get("market_rules_supported") is True,
        "nonempty_preregistered_conditions": bool(conditions),
    }
    try:
        preregistered_at = _aware(policy.get("preregistered_at_utc"), "preregistered_at_utc")
        evaluation_boundary = datetime.combine(eval_start, datetime.min.time(), tzinfo=_TAIPEI).astimezone(timezone.utc)
        quality_gates["thresholds_preregistered"] = quality_gates["thresholds_preregistered"] and preregistered_at < evaluation_boundary
    except ValueError:
        quality_gates["thresholds_preregistered"] = False
    enough_samples = eval_count >= min_eval and train_count >= min_train
    claim_allowed = enough_samples and all(quality_gates.values())
    if not conditions:
        exclusions["thresholds_not_pre_registered"] = len(signals)
    status = "sample_ready_for_review" if claim_allowed else "insufficient_evidence"
    evaluation_mode = dataset.get("evaluation_mode", "screen_evaluation")
    evaluation_performed = bool(conditions) and bool(signals)
    eval_returns = [Decimal(row["outcome"]["relative_net_percentage_points"]) for row in evaluation]
    mean_relative = None
    if eval_returns:
        with localcontext() as context:
            context.prec = 48
            mean_relative = str(sum(eval_returns, Decimal(0)) / Decimal(len(eval_returns)))
    eval_entries = sorted({row["outcome"]["entry_date"] for row in evaluation})
    eval_exits = sorted({row["outcome"]["exit_date"] for row in evaluation})
    policy_hash = canonical_sha256({"conditions": dataset.get("conditions"), "scope": dataset.get("scope"), "evaluation_policy": policy, "quality": quality})
    evaluation_hash = canonical_sha256({"dataset_sha256": input_hash, "evaluation_policy_sha256": policy_hash})
    report = {
        "schema_version": "panwatch.pw13.evaluation.v1",
        "status": status,
        "evaluation_mode": evaluation_mode,
        "evaluation_performed": evaluation_performed,
        "insufficient_evidence_reason": (
            "inventory_only_no_preregistered_thresholds_or_forward_outcome_evaluation"
            if evaluation_mode == "inventory_only" else
            "no_preregistered_conditions" if not conditions else
            "sample_floor_or_quality_gates_not_met" if not claim_allowed else None
        ),
        "evidence_kind": dataset.get("evidence_kind", "unspecified"),
        "efficacy_claim_allowed": False,
        "sample_floor_and_quality_gates_passed": claim_allowed,
        "dataset_sha256": input_hash,
        "evaluation_policy_sha256": policy_hash,
        "evaluation_sha256": evaluation_hash,
        "scope": {"instrument_ids": selected_scope, "universe_basis": dataset.get("scope", {}).get("universe_basis")},
        "decision_policy": {
            "decision_time_source": "per-signal timezone-aware decision_at_utc",
            "factor_availability": "latest admissible period and semantic version known by decision time; no backdating from report/data period",
            "same_day_close_allowed": False,
            "entry_rule": "first retained close strictly after decision date",
            "outcome_horizon_sessions": horizon,
            "calendar_basis": "retained observations; exchange calendar verification required separately",
            "venue_benchmark_mapping": dict(_BENCHMARK_FOR_VENUE),
            "train_range": {"start": train_start.isoformat(), "end": train_end.isoformat()},
            "evaluation_range": {"start": eval_start.isoformat(), "end": eval_end.isoformat()},
            "purge": "drop training outcomes whose exit date is on or after evaluation start",
            "thresholds": dataset.get("conditions"),
            "thresholds_preregistered": quality_gates["thresholds_preregistered"],
            "preregistered_at_utc": policy.get("preregistered_at_utc"),
            "cost_policy": dict(cost_policy),
        },
        "counts": {
            "input_signals": len(signals),
            "eligible_selected_signals_with_outcomes": train_count + eval_count,
            "train_samples_after_purge": train_count,
            "evaluation_samples": eval_count,
            "purged_training_signals": sum(1 for row in signal_results if row.get("purged") and row.get("split") == "train"),
            "purged_evaluation_signals": sum(1 for row in signal_results if row.get("purged") and row.get("split") == "evaluation"),
            "factor_values_selected_by_condition": factor_selection_counts,
            "availability_audit_rows": len(availability_audit),
            "available_values_by_condition_at_fixed_decisions": {
                name: sum(1 for row in availability_audit if row["conditions"][name]["status"] == "available_by_decision")
                for name in audit_conditions
            },
            "minimum_train_samples": min_train,
            "minimum_evaluation_samples": min_eval,
        },
        "outcome_summary": {
            "evaluation_mean_relative_net_percentage_points": mean_relative,
            "evaluation_entry_dates": eval_entries,
            "evaluation_exit_dates": eval_exits,
            "interpretation": "descriptive_only; a separate reviewed factor study is required for efficacy claims",
        },
        "quality_gates": quality_gates,
        "exclusions": dict(sorted(exclusions.items())),
        "signals": signal_results,
        "availability_audit": availability_audit,
        "limitations": [
            "Raw-price outcomes exclude dividends and do not apply corporate actions.",
            "A retained observation sequence is not a verified exchange-session calendar.",
            "Fixed selectors are not a point-in-time market universe.",
            "Basis-point costs omit brokerage minimums, taxes, lot sizing, market impact and execution rules unless separately supported.",
            "Synthetic evidence can validate code paths but never supports efficacy claims.",
            "This evaluator reports descriptive samples only; a separate reviewed factor study is required before any efficacy statement.",
        ],
    }
    report["report_sha256"] = canonical_sha256(report)
    return report
