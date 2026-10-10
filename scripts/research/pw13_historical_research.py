#!/usr/bin/env python3
"""Build the checked-evidence PW-13 inventory or evaluate an offline snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.modules.research.taiwan_historical_research import (  # noqa: E402
    canonical_sha256,
    evaluate_dataset,
)


EVIDENCE_ROOT = Path("docs/plans/twmd-integration/evidence")
SOURCE_PATHS = [
    EVIDENCE_ROOT / "PW-05-shared-service-2026-10-07.json",
    EVIDENCE_ROOT / "PW-10-shared-service-2026-10-07.json",
    EVIDENCE_ROOT / "PW-11-shared-service-2026-10-08.json",
    EVIDENCE_ROOT / "PW-12-shared-service-2026-10-07.json",
    Path("packages/marketdata/tests/fixtures/twmd/captured/2026-10-06.json"),
    Path("packages/marketdata/tests/fixtures/twmd/captured/financial-statements-twse-2330-2024q4.json.metadata.json"),
    Path("packages/marketdata/tests/fixtures/twmd/captured/financial-statements-twse-2330-2024q4.json"),
]
DECISION_SCOPE = {
    "start_date": "2026-10-02",
    "end_date": "2026-10-07",
    "decision_grid_taipei": [
        "2026-10-02T17:00:00+08:00",
        "2026-10-05T17:00:00+08:00",
        "2026-10-07T09:00:00+08:00",
    ],
    "train_start_date": "2026-10-02",
    "train_end_date": "2026-10-05",
    "eval_start_date": "2026-10-07",
    "eval_end_date": "2026-10-07",
    "outcome_horizon_sessions": 5,
    "entry_rule": "first retained official close strictly after the decision date",
    "minimum_train_samples": 30,
    "minimum_eval_samples": 30,
    "cost_scenario": {
        "unit": "basis_points_per_side",
        "entry_cost_bps": "5",
        "exit_cost_bps": "5",
        "market_rules_supported": False,
        "interpretation": "sensitivity assumption only; omits TW commissions, tax, minimum fees, lot sizing and impact",
    },
}
AUDIT_CONDITIONS = [
    "pe_max", "pb_max", "dividend_yield_min_pct",
    "revenue_yoy_min_pct", "institutional_net_min_shares",
]


def _read(root: Path, relative: Path) -> dict[str, Any]:
    # Preserve the source's exact decimal token instead of routing through a
    # binary float before the evaluator converts it to Decimal.
    return json.loads((root / relative).read_text(encoding="utf-8"), parse_float=str)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _month(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:7]


def _safe_block(block: dict[str, Any] | None) -> dict[str, Any]:
    block = block or {}
    freshness = block.get("freshness") or {}
    coverage = freshness.get("coverage") or {}
    return {
        "status": block.get("status", "unknown"),
        "reason": block.get("reason"),
        "data_period": freshness.get("data_period"),
        "source_contract": block.get("source_contract"),
        "dataset_coverage": coverage.get("dataset_coverage"),
        "selected_instrument_presence": coverage.get("selected_instrument_presence"),
        "requested_scope": coverage.get("requested_scope"),
        "source_received_at_utc": freshness.get("source_received_at_utc"),
        "first_observed_at": freshness.get("first_observed_at"),
        "source_served_at": freshness.get("source_served_at"),
        "report_date": freshness.get("report_date"),
        "publication_time": freshness.get("publication_time"),
        "age_status": freshness.get("age_status"),
        "months": [
            {"data_month": _month(item.get("data_month")), "presence": item.get("presence"), "retained_row": item.get("retained_row")}
            for item in block.get("months", [])
        ],
        "trade_dates": (block.get("period") or {}).get("trade_dates", []),
    }


def _selected_research_inventory(pw05: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for result in pw05.get("results", []):
        blocks = result.get("blocks") or {}
        rows.append({
            "instrument_id": result.get("instrument_id"),
            "security_type": (result.get("instrument") or {}).get("security_type"),
            "is_active": (result.get("instrument") or {}).get("is_active"),
            "selectors": result.get("selectors"),
            "datasets": {
                "valuation": _safe_block(blocks.get("valuation")),
                "institutional_flows": _safe_block(blocks.get("institutional_flows")),
                "monthly_revenues": _safe_block(blocks.get("monthly_revenues")),
                "company_profile": _safe_block(blocks.get("company_profile")),
            },
        })
    return rows


def _benchmark_inventory(pw11: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in pw11.get("observations", []):
        comparison = (item.get("benchmark_comparison") or {}).get("data") or {}
        stock = comparison.get("stock_series") or {}
        benchmark = comparison.get("benchmark_series") or {}
        provenance = (item.get("benchmark_comparison") or {}).get("evidence") or {}
        stock_source = provenance.get("stock_source") or {}
        benchmark_source = provenance.get("benchmark_source") or {}
        dates = comparison.get("observations") or []
        receipts: dict[tuple, dict[str, Any]] = {}
        for receipt in benchmark_source.get("bar_receipts") or []:
            key = (
                receipt.get("revision"), receipt.get("capture_id"),
                receipt.get("captured_at"), receipt.get("payload_sha256"),
            )
            receipts[key] = {
                "revision": key[0], "capture_id": key[1], "captured_at": key[2], "payload_sha256": key[3],
            }
        result.append({
            "instrument_id": item.get("instrument_id"),
            "venue": comparison.get("venue"),
            "benchmark_id": comparison.get("benchmark_id"),
            "requested_range": comparison.get("requested_range"),
            "stock_series": {
                "provider": stock.get("provider"),
                "dataset": stock_source.get("dataset"),
                "adjustment_mode": stock.get("adjustment_mode"),
                "returned_count": stock.get("returned_count"),
                "partial": stock.get("partial"),
                "oldest_returned_trade_date": stock_source.get("oldest_returned_trade_date"),
                "latest_returned_trade_date": stock_source.get("latest_returned_trade_date"),
                "requested_period_returned_count": stock_source.get("requested_period_returned_count"),
                "requested_period_returned_dates": stock_source.get("requested_period_returned_dates", []),
                "observation_receipts": [
                    {"trade_date": row.get("trade_date"), "acquired_at": (row.get("stock_coverage") or {}).get("acquired_at"), "partition": (row.get("stock_coverage") or {}).get("partition_key"), "dataset_status": (row.get("stock_coverage") or {}).get("status")}
                    for row in dates
                ],
            },
            "benchmark_series": {
                "provider": benchmark.get("provider"),
                "source_contract": benchmark_source.get("source_contract"),
                "source_alias": benchmark_source.get("source_alias"),
                "source_url": benchmark_source.get("source_url"),
                "unit": benchmark_source.get("unit"),
                "basis": benchmark_source.get("basis"),
                "returned_count": benchmark_source.get("returned_count"),
                "total_count": benchmark_source.get("total_count"),
                "partial": benchmark_source.get("partial"),
                "truncated": benchmark_source.get("truncated"),
                "coverage_window": benchmark_source.get("coverage_window"),
                "source_reported_gap_dates": benchmark_source.get("gaps", []),
                "gap_interpretation": "source-reported calendar selectors; not a verified exchange-session gap list",
                "observed_dates": [row.get("trade_date") for row in dates],
                "capture_receipts": sorted(receipts.values(), key=lambda row: (row.get("captured_at") or "", row.get("capture_id") or "")),
            },
            "comparison_observation_count": len(dates),
            "interpretation": "common actual raw-price observations only; source calendar gaps are not a verified exchange calendar",
        })
    return result


def _home_index_inventory(pw11: dict[str, Any]) -> list[dict[str, Any]]:
    home = pw11.get("home") or {}
    return [
        {
            "benchmark_id": row.get("symbol"),
            "provider": row.get("provider"),
            "source_alias": row.get("source_alias"),
            "unit": row.get("unit"),
            "basis": row.get("basis"),
            "trade_date": row.get("trade_date"),
            "observed_at": home.get("observed_at"),
            "spark_observation_count": len(row.get("spark_dates") or []),
            "spark_observed_dates": row.get("spark_dates") or [],
            "partial": row.get("source_partial"),
            "truncated": row.get("source_truncated"),
        }
        for row in home.get("indices", [])
    ]


def _financial_revision_inventory(pw10: dict[str, Any], root: Path) -> dict[str, Any]:
    report = ((pw10.get("financial_statements") or {}).get("report") or {})
    selectors = pw10.get("selectors") or {}
    first_seen = report.get("semantic_revision_first_observed_at_utc")
    try:
        first_seen_year = date.fromisoformat(first_seen[:10]).year if first_seen else None
    except ValueError:
        first_seen_year = None
    metadata_path = root / Path("packages/marketdata/tests/fixtures/twmd/captured/financial-statements-twse-2330-2024q4.json.metadata.json")
    raw_path = metadata_path.with_name("financial-statements-twse-2330-2024q4.json")
    raw_sha = _sha256(raw_path) if raw_path.exists() else None
    return {
        "instrument_id": pw10.get("instrument_id"),
        "fiscal_year": selectors.get("fiscal_year"),
        "fiscal_quarter": selectors.get("fiscal_quarter"),
        "report_scope": (pw10.get("financial_statements") or {}).get("report_scope", "consolidated"),
        "total_fact_count": (pw10.get("financial_statements") or {}).get("total_fact_count"),
        "returned_fact_count": (pw10.get("financial_statements") or {}).get("returned_fact_count"),
        "published_at_utc": report.get("published_at_utc"),
        "document_first_observed_at_utc": report.get("document_first_observed_at_utc"),
        "semantic_revision_first_observed_at_utc": first_seen,
        "original_received_at_utc": report.get("original_received_at_utc"),
        "revision_id": report.get("semantic_revision_id"),
        "capture_id": report.get("capture_id"),
        "source_sha256": report.get("raw_sha256"),
        "captured_raw_fixture_sha256": raw_sha,
        "point_in_time_eligible_for_2024_decisions": bool(first_seen and first_seen_year is not None and first_seen_year <= 2024 and report.get("published_at_utc")),
        "ruling": "2024Q4 content first observed/received in Oct 2026 with no publisher time; exclude from 2024 decision-time conditions. Document-first and semantic-revision-first clocks remain separate.",
    }


def _captured_factor_observations(captured: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    """Keep exact captured factor values and bind unversioned rows to the local snapshot."""
    relative = Path("packages/marketdata/tests/fixtures/twmd/captured/2026-10-06.json")
    snapshot_sha = _sha256(root / relative)
    local_snapshot_at = captured.get("additional_capture_at_taipei") or captured.get("captured_at_taipei")
    factors: list[dict[str, Any]] = []

    def add(instrument_id, condition, period, value, unit, *, contract, row=None, receipt=None, source_hash=None, revision=None, capture_id=None, first_observed=None):
        item = {
            "instrument_id": instrument_id,
            "condition": condition,
            "data_period": period,
            "value": value,
            "unit": unit,
            "source_contract": contract or "twmd captured query response",
            "source_dataset": {
                "pe_max": "valuation", "pb_max": "valuation",
                "dividend_yield_min_pct": "valuation",
                "revenue_yoy_min_pct": "monthly_revenue",
                "institutional_net_min_shares": "institutional_flows",
            }[condition],
            "presence": "present" if value is not None else "missing",
        }
        if receipt and source_hash:
            item.update({
                "source_received_at_utc": receipt,
                "receipt_binds_content_version": True,
            })
        else:
            item["local_snapshot_at_utc"] = local_snapshot_at
            item["snapshot_sha256"] = snapshot_sha
        if source_hash:
            item["payload_sha256"] = source_hash
        if revision is not None:
            item["revision"] = revision
        if capture_id:
            item["capture_id"] = capture_id
        if first_observed:
            # Retain the row-observation clock as descriptive evidence. It is not
            # used as a semantic revision clock by the evaluator.
            item["row_first_observed_at_utc"] = first_observed
        if row and row.get("content_hash"):
            item["content_hash"] = row["content_hash"]
        factors.append(item)

    for case in captured.get("cases", []):
        label = case.get("label", "")
        tokens = label.split()
        if not tokens or ":" not in tokens[0]:
            continue
        instrument_id = tokens[0]
        match = re.search(r"20\d{2}-\d{2}-\d{2}", label)
        response = case.get("response")
        if " valuation " in label:
            row = response[0] if isinstance(response, list) and response else None
            period = row.get("trade_date") if row else (match.group(0) if match else None)
            if not period:
                continue
            contract = (row or {}).get("source_contract") or f"GET /api/v1/valuations ({instrument_id.split(':', 1)[0]})"
            for condition, field, unit in (
                ("pe_max", "pe_ratio", "ratio"),
                ("pb_max", "pb_ratio", "ratio"),
                ("dividend_yield_min_pct", "dividend_yield_pct", "percent"),
            ):
                add(
                    instrument_id, condition, period, (row or {}).get(field), unit,
                    contract=contract, row=row,
                    receipt=(row or {}).get("received_at_utc"),
                    source_hash=(row or {}).get("payload_sha256"),
                    revision=(row or {}).get("revision"), capture_id=(row or {}).get("capture_id"),
                )
        elif " institutional flow " in label and isinstance(response, dict):
            period = match.group(0) if match else None
            if not period:
                continue
            data = response.get("data") or []
            row = data[0] if data else None
            coverage = next((item for item in response.get("coverage", []) if item.get("trade_date") == period), {})
            contract = response.get("source_contract") or f"GET /api/v1/institutional-flows ({instrument_id.split(':', 1)[0]})"
            add(
                instrument_id, "institutional_net_min_shares", period,
                (row or {}).get("total_institutional_net_shares"), "shares",
                contract=contract, row=row,
                receipt=coverage.get("acquired_at") or coverage.get("received_at_utc") or (row or {}).get("received_at_utc"),
                source_hash=coverage.get("sha256") or coverage.get("payload_sha256") or (row or {}).get("payload_sha256"),
                revision=(row or {}).get("revision") or coverage.get("revision"),
                capture_id=(row or {}).get("capture_id") or coverage.get("capture_id"),
                first_observed=(row or {}).get("first_observed_at"),
            )
        elif " monthly revenue " in label and isinstance(response, dict):
            for month in response.get("months", []):
                period = month.get("data_month")
                if not period:
                    continue
                row = month.get("row") or {}
                add(
                    instrument_id, "revenue_yoy_min_pct", period[:7], row.get("year_over_year_pct"), "percent",
                    contract=row.get("source_contract") or "GET /api/v1/monthly-revenues",
                    row=row,
                    receipt=row.get("received_at_utc"),
                    source_hash=row.get("payload_sha256"),
                    revision=row.get("revision"), capture_id=row.get("capture_id"),
                )
    return factors


def _fixed_decision_signals(instrument_ids: list[str]) -> list[dict[str, str]]:
    utc_grid = [
        "2026-10-02T09:00:00Z",  # 17:00 Taipei, after the session close
        "2026-10-05T09:00:00Z",  # 17:00 Taipei, after the session close
        "2026-10-07T01:00:00Z",  # 09:00 Taipei, before that day's close
    ]
    return [
        {"instrument_id": instrument_id, "decision_at_utc": decision_at}
        for decision_at in utc_grid
        for instrument_id in instrument_ids
    ]


def build_checked_evidence_dataset(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize only committed, safe PW evidence into a reproducible bounded input."""
    documents = {relative.name: _read(root, relative) for relative in SOURCE_PATHS}
    pw05 = documents["PW-05-shared-service-2026-10-07.json"]
    pw10 = documents["PW-10-shared-service-2026-10-07.json"]
    pw11 = documents["PW-11-shared-service-2026-10-08.json"]
    pw12 = documents["PW-12-shared-service-2026-10-07.json"]
    captured = documents["2026-10-06.json"]
    instrument_ids = sorted({row.get("instrument_id") for row in pw05.get("results", [])} | {row.get("instrument_id") for row in pw11.get("observations", [])})
    source_inputs = [
        {"path": relative.as_posix(), "sha256": _sha256(root / relative)}
        for relative in SOURCE_PATHS
    ]
    profile = []
    revenue = []
    daily = []
    for row in _selected_research_inventory(pw05):
        instrument = row["instrument_id"]
        datasets = row["datasets"]
        profile.append({"instrument_id": instrument, **datasets["company_profile"]})
        revenue.append({"instrument_id": instrument, **datasets["monthly_revenues"]})
        for block in ("valuation", "institutional_flows"):
            daily.append({"instrument_id": instrument, "dataset": block, **datasets[block]})
    action_data = (pw12.get("corporate_actions") or {}).get("data") or {}
    action_coverage = {
        "instrument_id": pw12.get("instrument_id"),
        "selectors": pw12.get("selectors"),
        "ex_right_dividend": (action_data.get("ex_right_dividend") or {}).get("dataset_coverage", "unknown"),
        "capital_reduction": (action_data.get("capital_reduction") or {}).get("dataset_coverage", "unknown"),
        "known_event_count": len(action_data.get("known_event_dates") or []),
        "interpretation": "empty results have unknown coverage and are not proof of no company action",
    }
    financial = _financial_revision_inventory(pw10, root)
    inventory = {
        "schema_version": "panwatch.pw13.checked-evidence-inventory.v1",
        "evidence_as_of": "2026-10-08",
        "evidence_kind": "committed_official_service_and_capture_evidence",
        "input_files": source_inputs,
        "fixed_instrument_scope": instrument_ids,
        "daily_research_windows": daily,
        "monthly_revenue_windows": revenue,
        "latest_only_profile_observations": profile,
        "raw_price_and_venue_benchmark": _benchmark_inventory(pw11),
        "home_daily_index_captures": _home_index_inventory(pw11),
        "retained_factor_observations": _captured_factor_observations(captured, root),
        "fixed_availability_decision_grid_taipei": DECISION_SCOPE["decision_grid_taipei"],
        "financial_statement_revision": financial,
        "corporate_action_coverage": action_coverage,
        "coverage_gaps": [
            "PW-05 daily valuation/flow selectors are only 2026-10-02..05 calendar bounds; 2026-10-03/04 MISSING is not a verified session gap.",
            "The selected TWSE:2330 and TPEX:5347 monthly-revenue window covers 2026-07..08 with July missing and August present; it does not provide a multi-year YoY factor history.",
            "The reviewed upstream contracts do not establish a retained historical TPEX revenue series; exclude TPEX revenue-based historical scope until versioned receipts exist.",
            "Profile observations are latest-only. They cannot reconstruct a point-in-time historical security universe.",
            "TAIEX benchmark evidence is a bounded 24-observation capture; TPEX benchmark evidence has four latest-only observations. Neither is a complete calendar/session history.",
            "Corporate-action endpoints report unknown coverage. Raw-price outcomes cannot be interpreted as total returns.",
            "Only the bounded 2026-10-06 captured response retains exact factor values. The fixed decision grid audits availability but has no preregistered PW-06 thresholds or matched forward outcomes; it is inventory_only, not an efficacy evaluation.",
        ],
    }
    dataset = {
        "schema_version": "panwatch.pw13.snapshot.v1",
        "dataset_id": "pw13-checked-evidence-2026-10-08",
        "evidence_kind": "official_retained_evidence_summary",
        "source_evidence": source_inputs,
        "scope": {
            "instrument_ids": instrument_ids,
            "universe_kind": "fixed_selectors",
            "universe_basis": "fixed selectors observed in PW-05/PW-11; not a historical point-in-time universe",
            "decision_window": {"start_date": DECISION_SCOPE["start_date"], "end_date": DECISION_SCOPE["end_date"]},
        },
        "evaluation_mode": "inventory_only",
        "conditions": {},
        "availability_audit_conditions": AUDIT_CONDITIONS,
        "factor_observations": inventory["retained_factor_observations"],
        "signals": _fixed_decision_signals(instrument_ids),
        "stock_bars": [],
        "benchmark_bars": [],
        "evaluation_policy": {
            "train_start_date": DECISION_SCOPE["train_start_date"],
            "train_end_date": DECISION_SCOPE["train_end_date"],
            "eval_start_date": DECISION_SCOPE["eval_start_date"],
            "eval_end_date": DECISION_SCOPE["eval_end_date"],
            "outcome_horizon_sessions": DECISION_SCOPE["outcome_horizon_sessions"],
            "minimum_train_samples": DECISION_SCOPE["minimum_train_samples"],
            "minimum_eval_samples": DECISION_SCOPE["minimum_eval_samples"],
            "thresholds_preregistered": False,
            "cost_policy": DECISION_SCOPE["cost_scenario"],
        },
        "quality": {
            "point_in_time_universe_verified": False,
            "exchange_calendar_verified": False,
            "corporate_actions_complete": False,
        },
    }
    inventory["input_sha256"] = canonical_sha256(dataset)
    inventory["evaluation"] = evaluate_dataset(dataset)
    inventory["decision_and_cost_policy"] = DECISION_SCOPE
    return dataset, inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="evaluate one immutable offline PW-13 snapshot JSON")
    parser.add_argument("--output", type=Path, help="write JSON output to this path; stdout by default")
    parser.add_argument("--export-snapshot", type=Path, help="retain the evaluated dataset; refuses to overwrite different content")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.output and any(args.output.resolve() == path.resolve() for path in (args.input, args.export_snapshot) if path):
        parser.error("output report must use a different path from input and retained snapshots")
    if args.input:
        if args.input.stat().st_size > 16 * 1024 * 1024:
            parser.error("input snapshot must be at most 16 MiB")
        dataset = json.loads(args.input.read_text(encoding="utf-8"), parse_float=str)
        result = evaluate_dataset(dataset)
    else:
        dataset, result = build_checked_evidence_dataset(args.root)
    if args.export_snapshot:
        snapshot = json.dumps(dataset, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        args.export_snapshot.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.export_snapshot.open("x", encoding="utf-8") as handle:
                handle.write(snapshot)
        except FileExistsError:
            if args.export_snapshot.read_text(encoding="utf-8") != snapshot:
                parser.error("snapshot already exists with different content; choose a new path")
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
