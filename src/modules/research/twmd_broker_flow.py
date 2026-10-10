"""Source-preserving adapter for canonical TWMD broker-flow research."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
import re
import time
from typing import Any

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdBrokerFlowCoverageRead,
    TwmdBrokerFlowPriceLevelsRead,
    TwmdBrokerFlowQuantityRead,
)

from src.modules.research.twmd_profile_revenue import ResearchDataBlock


_CUTOVER = date(2026, 7, 24)
_MAX_QUANTITY_DAYS = 31
_MAX_COVERAGE_DAYS = 366
_TOP_N = 5


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _quantity_payload(row) -> dict[str, Any]:
    payload = asdict(row)
    payload["buy_vwap"] = _decimal_text(row.buy_vwap)
    payload["sell_vwap"] = _decimal_text(row.sell_vwap)
    return payload


def _price_payload(row) -> dict[str, Any]:
    payload = asdict(row)
    payload["price"] = str(row.price)
    return payload


def _source_failure(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, TwmdReadError):
        status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
        reason = exc.reason_code or (f"http_{status}" if status else "provider_error")
        if reason in {"invalid_response", "transport_error", "timeout", "provider_error"}:
            return reason, status
        if re.fullmatch(r"http_\d{3}", reason):
            number = int(reason[-3:])
            return (reason, status or number) if 100 <= number <= 599 else ("provider_error", status)
        return (f"http_{status}" if status else "provider_error"), status
    if isinstance(exc, TimeoutError):
        return "timeout", None
    if isinstance(exc, ValueError):
        return "invalid_response", None
    return "provider_error", None


def _failure_state(exc: Exception) -> dict[str, Any]:
    reason, status = _source_failure(exc)
    return {"status": "error", "reason": reason, "http_status": status, "data": []}


def _call_read(method, *args, today_taipei: date, deadline_monotonic: float | None):
    timeout = None
    if deadline_monotonic is not None:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("research deadline elapsed")
        config = getattr(getattr(method, "__self__", None), "config", {})
        configured = config.get("timeout_sec") or 5.0
        try:
            timeout = min(float(configured), remaining, 20.0)
        except (TypeError, ValueError):
            timeout = min(5.0, remaining)
    return method(*args, today_taipei=today_taipei, timeout_sec=timeout)


def _ratio_text(numerator: int, denominator: int) -> str | None:
    if denominator <= 0:
        return None
    value = Decimal(numerator) * Decimal(100) / Decimal(denominator)
    return format(value.quantize(Decimal("0.0001")), "f")


def _expected_provider_dates(provider: str, start: date, end: date) -> set[str]:
    if provider == "capital":
        last = min(end, _CUTOVER - timedelta(days=1))
        first = start
    else:
        first = max(start, _CUTOVER)
        last = end
    if first > last:
        return set()
    return {
        (first + timedelta(days=offset)).isoformat()
        for offset in range((last - first).days + 1)
    }


def _revision_consistency(quantity_rows: list[dict], coverage_rows: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    quantity_revisions: dict[tuple[str, str], set[str]] = {}
    for row in quantity_rows:
        revision = row.get("revision_id")
        if revision:
            quantity_revisions.setdefault((row["provider"], row["trade_date"]), set()).add(str(revision))
    coverage_revisions = {
        (row["provider"], row["trade_date"]): str(row["revision_id"])
        for row in coverage_rows if row.get("revision_id")
    }
    for key in set(quantity_revisions) | set(coverage_revisions):
        quantity = quantity_revisions.get(key, set())
        coverage = coverage_revisions.get(key)
        if not quantity or not coverage:
            state = "unknown"
        elif quantity == {coverage}:
            state = "consistent"
        else:
            state = "conflict"
        result[f"{key[0]}:{key[1]}"] = state
    return result


def _quantity_groups(
    quantity_rows: list[dict],
    coverage_rows: list[dict],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    for row in quantity_rows:
        group_key = (row["provider"], row["native_unit"], row["precision_shares"])
        branches = grouped.setdefault(group_key, {})
        branch = branches.setdefault(row["source_branch_key"], {
            "source_branch_key": row["source_branch_key"],
            "branch_code": row["branch_code"],
            "branch_name": row["branch_name"],
            "buy_native": 0,
            "sell_native": 0,
            "net_native": 0,
            "revisions": set(),
        })
        branch["buy_native"] += row["buy_native"]
        branch["sell_native"] += row["sell_native"]
        branch["net_native"] += row["net_native"]
        branch["branch_name"] = row["branch_name"]
        if row.get("revision_id"):
            branch["revisions"].add(row["revision_id"])

    result = []
    for (provider, unit, precision), branches in sorted(grouped.items()):
        expected_dates = _expected_provider_dates(provider, start, end)
        applicable_coverage = [
            row for row in coverage_rows
            if row["provider"] == provider and row["trade_date"] in expected_dates
        ]
        by_date = {row["trade_date"]: row for row in applicable_coverage}
        missing_dates = sorted(
            (expected_dates - set(by_date))
            | {row["trade_date"] for row in applicable_coverage if row["status"] in {"MISSING", "FAILED"}}
        )
        source_rows = [row for row in quantity_rows if row["provider"] == provider]
        consistency = _revision_consistency(source_rows, applicable_coverage)
        reconciliation_dates = sorted(
            day for day, coverage in by_date.items()
            if coverage["record_count"] != sum(row["trade_date"] == day for row in source_rows)
            or consistency.get(f"{provider}:{day}") == "conflict"
        )
        coverage_counts: dict[str, int] = {}
        for row in applicable_coverage:
            coverage_counts[row["status"]] = coverage_counts.get(row["status"], 0) + 1
        coverage_complete = (
            not missing_dates
            and not reconciliation_dates
            and not any(row["status"] in {"MISSING", "FAILED"} for row in applicable_coverage)
        )
        branch_rows = list(branches.values())
        buy_denominator = sum(row["buy_native"] for row in branch_rows)
        sell_denominator = sum(row["sell_native"] for row in branch_rows)
        top_buy_rows = sorted(branch_rows, key=lambda row: (-row["buy_native"], row["source_branch_key"]))[:_TOP_N]
        top_sell_rows = sorted(branch_rows, key=lambda row: (-row["sell_native"], row["source_branch_key"]))[:_TOP_N]

        def ranked(side: str) -> list[dict[str, Any]]:
            ordered = sorted(branch_rows, key=lambda row: (-row[side], row["source_branch_key"]))[:_TOP_N]
            return [
                {
                    **{key: row[key] for key in ("source_branch_key", "branch_code", "branch_name", "buy_native", "sell_native", "net_native")},
                    "revisions": sorted(row["revisions"]),
                    "share_of_observed_group_pct": _ratio_text(row[side], buy_denominator if side == "buy_native" else sell_denominator),
                }
                for row in ordered
            ]

        result.append({
            "provider": provider,
            "native_unit": unit,
            "precision_shares": precision,
            "top_buy": ranked("buy_native"),
            "top_sell": ranked("sell_native"),
            "top_n": _TOP_N,
            "top_n_buy_native": sum(row["buy_native"] for row in top_buy_rows),
            "top_n_sell_native": sum(row["sell_native"] for row in top_sell_rows),
            "top_n_buy_concentration_pct": _ratio_text(
                sum(row["buy_native"] for row in top_buy_rows), buy_denominator
            ),
            "top_n_sell_concentration_pct": _ratio_text(
                sum(row["sell_native"] for row in top_sell_rows), sell_denominator
            ),
            "observed_buy_denominator_native": buy_denominator,
            "observed_sell_denominator_native": sell_denominator,
            "denominator_definition": (
                "Sum of the reported native buy or sell quantity for every branch row returned "
                "by this provider, unit, precision, and requested range. It is an observed-source "
                "denominator, not an all-market denominator."
            ),
            "coverage_complete_for_source_dates": coverage_complete,
            "coverage_status_counts": coverage_counts,
            "coverage_missing_dates": missing_dates,
            "coverage_reconciliation_dates": reconciliation_dates,
            "revision_ids": sorted({revision for row in branch_rows for revision in row["revisions"]}),
        })
    return result


def _coverage_status_summary(rows: list[dict]) -> dict[str, Any]:
    by_provider: dict[str, dict[str, int]] = {}
    for row in rows:
        statuses = by_provider.setdefault(row["provider"], {})
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    return {provider: dict(sorted(statuses.items())) for provider, statuses in sorted(by_provider.items())}


def broker_flow_block(
    client,
    instrument_id: str,
    start_date: date,
    end_date: date,
    *,
    today_taipei: date,
    deadline_monotonic: float | None = None,
) -> ResearchDataBlock:
    """Read each endpoint independently and keep partial coverage visible."""
    selectors = {
        "instrument_id": instrument_id,
        "quantity_start": start_date.isoformat(),
        "quantity_end": end_date.isoformat(),
        "coverage_start": start_date.isoformat(),
        "coverage_end": end_date.isoformat(),
        "price_level_date": end_date.isoformat(),
    }

    if (end_date - start_date).days + 1 > _MAX_QUANTITY_DAYS:
        quantity_state = {
            "status": "unsupported",
            "reason": "quantity_range_exceeds_31_calendar_days",
            "http_status": None,
            "data": [],
        }
        quantity_read = None
    else:
        try:
            quantity_read = _call_read(
                client.broker_flow_quantities,
                instrument_id, start_date, end_date,
                today_taipei=today_taipei, deadline_monotonic=deadline_monotonic,
            )
            quantity_state = {"status": quantity_read.status, "reason": quantity_read.reason, "http_status": None}
        except Exception as exc:  # one broker endpoint cannot erase the other two
            quantity_read = None
            quantity_state = _failure_state(exc)

    try:
        coverage_read = _call_read(
            client.broker_flow_coverage,
            instrument_id, start_date, end_date,
            today_taipei=today_taipei, deadline_monotonic=deadline_monotonic,
        )
        coverage_state = {"status": coverage_read.status, "reason": coverage_read.reason, "http_status": None}
    except Exception as exc:
        coverage_read = None
        coverage_state = _failure_state(exc)

    try:
        price_read = _call_read(
            client.broker_flow_price_levels,
            instrument_id, end_date,
            today_taipei=today_taipei, deadline_monotonic=deadline_monotonic,
        )
        price_state = {"status": price_read.status, "reason": price_read.reason, "http_status": None}
    except Exception as exc:
        price_read = None
        price_state = _failure_state(exc)

    quantity_rows = [_quantity_payload(row) for row in quantity_read.data] if quantity_read else []
    coverage_rows = [asdict(row) for row in coverage_read.data] if coverage_read else []
    price_rows = [_price_payload(row) for row in price_read.data] if price_read else []
    groups = _quantity_groups(quantity_rows, coverage_rows, start_date, end_date)
    revisions = _revision_consistency(quantity_rows, coverage_rows)
    price_revision_ids = {row["revision_id"] for row in price_rows}
    quantity_price_revisions = {
        row["revision_id"] for row in quantity_rows
        if row["provider"] == "twse" and row["trade_date"] == end_date.isoformat() and row.get("revision_id")
    }
    if not price_rows or not quantity_price_revisions:
        price_revision_consistency = "unknown"
    elif len(price_revision_ids) == 1 and price_revision_ids == quantity_price_revisions:
        price_revision_consistency = "consistent"
    else:
        price_revision_consistency = "conflict"

    quantity_coverage_conflicts = [
        f"{row['provider']}:{row['trade_date']}"
        for row in coverage_rows
        if quantity_read is not None and (
            row["record_count"] != sum(
                item["provider"] == row["provider"] and item["trade_date"] == row["trade_date"]
                for item in quantity_rows
            )
            or revisions.get(f"{row['provider']}:{row['trade_date']}") == "conflict"
        )
    ]
    component_statuses = {quantity_state["status"], coverage_state["status"], price_state["status"]}
    coverage_has_result = any(row["status"] != "MISSING" for row in coverage_rows)
    has_data = bool(quantity_rows or price_rows or coverage_has_result)
    has_failure = "error" in component_statuses
    has_known_result = bool(component_statuses - {"error", "unknown"})
    if has_data:
        block_status = "partial" if (
            has_failure
            or "partial" in component_statuses
            or "unsupported" in component_statuses
            or "not_materialized" in component_statuses
            or coverage_state["status"] == "missing"
            or price_revision_consistency == "conflict"
            or bool(quantity_coverage_conflicts)
            or any(not group["coverage_complete_for_source_dates"] for group in groups)
        ) else "available"
    elif coverage_state["status"] == "missing":
        block_status = "missing"
    elif coverage_state["status"] in {"empty", "closed"}:
        block_status = coverage_state["status"]
    elif has_failure and has_known_result:
        block_status = "partial"
    elif has_failure:
        block_status = "error"
    elif "unsupported" in component_statuses:
        block_status = "unsupported"
    else:
        block_status = "unknown"

    price_no_rows_note = (
        "The endpoint returns an empty list for both EMPTY and FAILED materialized detail projections; "
        "a successful empty response cannot distinguish those outcomes."
        if price_state["reason"] == "materialized_no_rows_status_unknown" else None
    )
    data = {
        "instrument_id": instrument_id,
        "quantity_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "max_calendar_days": _MAX_QUANTITY_DAYS, **quantity_state},
        "quantity_observations": quantity_rows,
        "quantity_groups": groups,
        "coverage_range": {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "max_calendar_days": _MAX_COVERAGE_DAYS, **coverage_state},
        "coverage_observations": coverage_rows,
        "coverage_status_counts_by_provider": _coverage_status_summary(coverage_rows),
        "price_levels": {
            "trade_date": end_date.isoformat(),
            **price_state,
            "observations": price_rows,
            "revision_consistency_with_same_date_quantities": price_revision_consistency,
            "no_rows_interpretation": price_no_rows_note,
        },
    }
    consistency_warnings = [
        f"{key} quantity and coverage revisions conflict"
        for key, value in sorted(revisions.items()) if value == "conflict"
    ]
    if price_revision_consistency == "conflict":
        consistency_warnings.append("price-level and same-date quantity revisions conflict")
    for key in quantity_coverage_conflicts:
        consistency_warnings.append(f"{key} quantity count or revision conflicts with coverage")
    data["revision_consistency_warnings"] = consistency_warnings
    evidence = {
        "provider": "twmd",
        "instrument_id": instrument_id,
        "endpoint": ",".join((
            "/api/v1/broker-flow/quantities",
            "/api/v1/broker-flow/coverage",
            "/api/v1/broker-flow/price-levels",
        )),
        "selectors": selectors,
        "source_contract": "broker_flow",
        "cutover_date": _CUTOVER.isoformat(),
        "period": {"trade_dates": sorted({row["trade_date"] for row in quantity_rows})},
        "per_period_provenance": quantity_rows,
        "per_period_coverage": coverage_rows,
        "price_level_provenance": price_rows,
        "revision_consistency_by_provider_date": revisions,
        "revision_consistency_warnings": consistency_warnings,
        "source_report_date": None,
        "publication_time": None,
        "source_received_at_utc": None,
        "served_at": None,
        "units": {
            "quantities": "provider-native; Capital lots or TWSE shares, never converted or combined",
            "precision_shares": "reported native quantity precision metadata; not a share conversion",
            "vwap": "source buy/sell transaction VWAP in TWD per share; not position cost basis",
            "price_levels": "TWD per share with exact TWSE shares",
            "concentration": "percent of observed branch-side native quantity within one provider/unit/precision group",
        },
        "dataset_coverage": _coverage_status_summary(coverage_rows),
        "selected_instrument_presence": "present" if quantity_rows else "unknown",
        "revision": None,
        "capture_id": None,
        "payload_sha256": None,
        "denominator_limit": "Concentration uses only returned branch rows for a single provider-native group; it is not a market-wide measure, especially when source coverage is incomplete.",
        "vwap_meaning": "Buy/sell transaction average reported by the source; it does not describe branch inventory cost basis.",
        "price_level_empty_semantics": "A successful empty detail response means an EMPTY or FAILED projection exists; the endpoint does not identify which status.",
        "provenance_limit": "Canonical broker-flow responses expose per-row revision IDs but no source receipt, capture ID, or publisher publication time.",
    }
    http_statuses = {
        "quantity": quantity_state.get("http_status"),
        "coverage": coverage_state.get("http_status"),
        "price_levels": price_state.get("http_status"),
    }
    if any(http_statuses.values()):
        evidence["endpoint_http_statuses"] = http_statuses
    failed_reasons = {
        state["reason"] for state in (quantity_state, coverage_state, price_state)
        if state["status"] == "error"
    }
    primary_reason = (
        next(iter(failed_reasons)) if has_failure and len(failed_reasons) == 1 and len(component_statuses) == 1 else
        "some_broker_flow_endpoints_failed" if has_failure else
        "broker_flow_source_scope_limited" if block_status in {"partial", "unsupported"} else
        coverage_state["reason"] if coverage_state["status"] in {"empty", "missing", "closed"} else
        "canonical_broker_flow_results"
    )
    return ResearchDataBlock(data=data, status=block_status, reason=primary_reason, evidence=evidence)


def broker_flow_read_error(exc: TwmdReadError, selectors: dict[str, str]) -> ResearchDataBlock:
    reason, status = _source_failure(exc)
    return ResearchDataBlock(
        data=None,
        status="error",
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": selectors.get("instrument_id"),
            "endpoint": "/api/v1/broker-flow",
            "selectors": selectors,
            "source_contract": "broker_flow",
            "period": None,
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {},
            "dataset_coverage": None,
            "selected_instrument_presence": None,
            "http_status": status,
        },
    )


def broker_flow_unsupported_block(instrument_id: str, reason: str) -> ResearchDataBlock:
    return ResearchDataBlock(
        data={
            "instrument_id": instrument_id,
            "quantity_range": {"status": "unsupported", "reason": reason, "data": []},
            "quantity_observations": [],
            "quantity_groups": [],
            "coverage_range": {"status": "unsupported", "reason": reason, "data": []},
            "coverage_observations": [],
            "coverage_status_counts_by_provider": {},
            "price_levels": {
                "status": "unsupported", "reason": reason,
                "trade_date": None, "observations": [],
                "revision_consistency_with_same_date_quantities": "unknown",
                "no_rows_interpretation": None,
            },
        },
        status="unsupported",
        reason=reason,
        evidence={
            "provider": "twmd",
            "instrument_id": instrument_id,
            "endpoint": ",".join((
                "/api/v1/broker-flow/quantities",
                "/api/v1/broker-flow/coverage",
                "/api/v1/broker-flow/price-levels",
            )),
            "selectors": {"instrument_id": instrument_id},
            "source_contract": "broker_flow",
            "cutover_date": _CUTOVER.isoformat(),
            "period": None,
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": None,
            "served_at": None,
            "units": {},
            "dataset_coverage": None,
            "selected_instrument_presence": None,
            "revision": None,
            "capture_id": None,
            "payload_sha256": None,
            "provenance_limit": "Broker-flow queries currently accept canonical four-digit TWSE IDs only.",
        },
    )
