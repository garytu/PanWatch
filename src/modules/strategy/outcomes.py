"""Runtime production and public summaries for immutable RC-D outcome populations."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging
import math
import re
import threading
from typing import Any

from sqlalchemy import and_, func, or_

from src.modules.strategy.outcome_kernel import (
    EVALUATOR_CANDIDATE_FORWARD_V2,
    EVALUATOR_EXECUTION_V2,
    EVALUATOR_SIGNAL_FORWARD_V2,
    EXECUTION_POLICY_VERSION,
    FORWARD_POLICY_VERSION,
    MAX_OBSERVATION_DAYS,
    POPULATION_CANDIDATE_FORWARD_V2,
    POPULATION_SIGNAL_FORWARD_V2,
    POPULATION_STRATEGY_EXECUTION_V2,
    RETRY_BACKOFF_HOURS,
    canonical_json,
    evaluate_execution_stock,
    evaluate_forward_stock,
    normalize_bar,
    retry_delay,
    sha256_json,
    _session_due,
)
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    OutcomeEvaluationAttempt,
    RankingSnapshotItem,
    StrategyOutcomeV2,
)
from src.platform.scheduling.timezone import utc_now
from src.platform.scheduling.trading_calendar import (
    is_trading_day,
    market_calendar_context,
    market_calendar_evidence,
)

logger = logging.getLogger(__name__)
_RUN_LOCK = threading.Lock()
DEFAULT_HORIZONS = (1, 3, 5, 10)
_SUPPORTED_POPULATIONS = {POPULATION_SIGNAL_FORWARD_V2, POPULATION_CANDIDATE_FORWARD_V2}
_SUPPORTED_VERSIONS = {EVALUATOR_SIGNAL_FORWARD_V2, EVALUATOR_CANDIDATE_FORWARD_V2}
_COST_POLICY_VERSION = "dated-cost-one-configured-lot-v1"
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
_MAX_SUPPORTED_HORIZON_SESSIONS = 30


def _db_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return _aware(parsed)


def _enum_market(market: str) -> MarketCode | None:
    try:
        return MarketCode(str(market or "").strip().upper())
    except ValueError:
        return None


def _session_policy(market: str) -> tuple[str, str] | None:
    code = _enum_market(market)
    definition = MARKETS.get(code) if code else None
    if not definition or not definition.sessions:
        return None
    return definition.timezone, definition.sessions[-1].end.strftime("%H:%M")


def _revision_evaluator(base: str, revision: str | None) -> str:
    """Give an explicit evaluator rollout its own append-only projection identity."""
    return f"{base}@{revision}" if revision else base


def _expected_sessions(market: str, session_date: str, count: int) -> tuple[list[str], dict]:
    """Read only exact exchange sessions from the currently loaded authoritative calendar."""
    try:
        start = date.fromisoformat(session_date)
        if start.isoformat() != session_date:
            raise ValueError("bad date")
    except (TypeError, ValueError):
        return [], {"status": "unknown", "reason_code": "decision_date_invalid"}
    found: list[str] = []
    target = start + timedelta(days=1)
    last = start
    # 60 calendar days gives room for 10 open sessions around holidays.
    while target <= start + timedelta(days=60) and len(found) < count:
        context = market_calendar_context(market, target)
        if context.get("status") != "known":
            return found, {"status": "unknown", "reason_code": "calendar_coverage_missing"}
        if is_trading_day(market, target):
            found.append(target.isoformat())
        last = target
        target += timedelta(days=1)
    if len(found) != count:
        return found, {"status": "unknown", "reason_code": "calendar_horizon_uncovered"}
    evidence = market_calendar_evidence(market, start, last)
    if evidence.get("status") != "known" or not evidence.get("calendar_version"):
        return found, {**evidence, "reason_code": "calendar_coverage_missing"}
    return found, evidence


def _decision_snapshot(item: RankingSnapshotItem) -> dict[str, Any]:
    return {
        "decision_snapshot_id": item.decision_snapshot_id,
        "stock_symbol": item.stock_symbol,
        "stock_market": item.stock_market,
        "instrument_id": item.instrument_id,
        "instrument_group": item.instrument_group,
        "instrument_authority": item.instrument_authority or {},
        "strategy_code": item.strategy_code,
        "candidate_id": item.candidate_id,
        "signal_run_id": item.signal_run_id,
        "session_date": item.session_date,
        "decision_at_utc": item.decision_at_utc.isoformat() if item.decision_at_utc else None,
        "population": item.outcome_population_id,
        "evaluation_version": item.evaluation_version,
        "point_in_time_status": item.point_in_time_status or "UNVERIFIABLE",
        "primary_horizon_sessions": item.primary_horizon_sessions,
        "score_version": item.score_version or item.ranker_version or "",
        "capture_hash": item.capture_hash,
        "action": item.action or "watch",
        "is_holding_snapshot": bool(item.is_holding_snapshot),
        "candidate_source": item.candidate_source or "",
        "entry_low": item.entry_low,
        "entry_high": item.entry_high,
        "stop_loss": item.stop_loss,
        "target_price": item.target_price,
        "ranking_value": item.ranking_value,
    }


def _decision_queue(db, *, cutoff: str, clock: datetime, limit: int,
                    horizons: set[int], evaluation_revision: str | None) -> list[dict[str, Any]]:
    """Select due stock/benchmark retries first, then genuinely unseeded decisions.

    Existing terminal/not-due rows must not consume the cap on every scheduler tick.
    The SQL predicates use archived primary horizons and explicit rollout identities.
    """
    base_filter = (
        RankingSnapshotItem.session_date >= cutoff,
        RankingSnapshotItem.outcome_population_id.in_(_SUPPORTED_POPULATIONS),
        RankingSnapshotItem.evaluation_version.in_(_SUPPORTED_VERSIONS),
    )
    now_db = _db_time(clock)
    due_stock = and_(
        StrategyOutcomeV2.stock_retryable.is_(True),
        ~StrategyOutcomeV2.evaluation_status.in_(("COMPLETE", "AMBIGUOUS")),
        or_(StrategyOutcomeV2.stock_next_retry_at.is_(None),
            StrategyOutcomeV2.stock_next_retry_at <= now_db,
            StrategyOutcomeV2.stock_retry_deadline_at <= now_db),
    )
    due_benchmark = and_(
        StrategyOutcomeV2.evaluation_status.in_(("COMPLETE", "AMBIGUOUS")),
        StrategyOutcomeV2.benchmark_retryable.is_(True),
        or_(StrategyOutcomeV2.benchmark_next_retry_at.is_(None),
            StrategyOutcomeV2.benchmark_next_retry_at <= now_db,
            StrategyOutcomeV2.benchmark_retry_deadline_at <= now_db),
    )
    requested_row_keys = []
    for source_version in _SUPPORTED_VERSIONS:
        forward_version = _revision_evaluator(source_version, evaluation_revision)
        execution_version = _revision_evaluator(EVALUATOR_EXECUTION_V2, evaluation_revision)
        forward_horizons = sorted(horizons | ({5} if source_version == EVALUATOR_SIGNAL_FORWARD_V2 else set()))
        requested_row_keys.append(and_(
            RankingSnapshotItem.evaluation_version == source_version,
            StrategyOutcomeV2.outcome_population_id == RankingSnapshotItem.outcome_population_id,
            StrategyOutcomeV2.evaluation_version == forward_version,
            or_(StrategyOutcomeV2.horizon_sessions.in_(forward_horizons),
                StrategyOutcomeV2.horizon_sessions == RankingSnapshotItem.primary_horizon_sessions),
        ))
        requested_row_keys.append(and_(
            RankingSnapshotItem.evaluation_version == source_version,
            StrategyOutcomeV2.outcome_population_id == POPULATION_STRATEGY_EXECUTION_V2,
            StrategyOutcomeV2.evaluation_version == execution_version,
            or_(StrategyOutcomeV2.horizon_sessions.in_(sorted(horizons)),
                StrategyOutcomeV2.horizon_sessions == RankingSnapshotItem.primary_horizon_sessions),
        ))
    due_rows = (
        db.query(StrategyOutcomeV2.decision_snapshot_id,
                 func.min(func.coalesce(StrategyOutcomeV2.stock_next_retry_at,
                                        StrategyOutcomeV2.benchmark_next_retry_at)),
                 func.min(StrategyOutcomeV2.id))
        .join(RankingSnapshotItem,
              RankingSnapshotItem.decision_snapshot_id == StrategyOutcomeV2.decision_snapshot_id)
        .filter(*base_filter, StrategyOutcomeV2.session_date >= cutoff,
                or_(due_stock, due_benchmark), or_(*requested_row_keys))
        .group_by(StrategyOutcomeV2.decision_snapshot_id)
        .order_by(func.min(func.coalesce(StrategyOutcomeV2.stock_next_retry_at,
                                         StrategyOutcomeV2.benchmark_next_retry_at)).asc().nullsfirst(),
                  func.min(StrategyOutcomeV2.id).asc())
        .limit(max(1, int(limit)))
        .all()
    )
    due_ids = [str(row[0]) for row in due_rows]
    due_items = (
        db.query(RankingSnapshotItem).filter(
            *base_filter, RankingSnapshotItem.decision_snapshot_id.in_(due_ids)
        ).all()
        if due_ids else []
    )
    due_by_id = {item.decision_snapshot_id: item for item in due_items}
    ordered_due = [due_by_id[key] for key in due_ids if key in due_by_id]
    remaining = max(0, int(limit) - len(ordered_due))
    if not remaining:
        return [_decision_snapshot(item) for item in ordered_due]

    missing_conditions = []
    for source_version in _SUPPORTED_VERSIONS:
        source_filter = RankingSnapshotItem.evaluation_version == source_version
        forward_version = _revision_evaluator(source_version, evaluation_revision)
        execution_version = _revision_evaluator(EVALUATOR_EXECUTION_V2, evaluation_revision)
        for horizon in horizons:
            for population, evaluator in (
                (RankingSnapshotItem.outcome_population_id, forward_version),
                (POPULATION_STRATEGY_EXECUTION_V2, execution_version),
            ):
                exists_row = db.query(StrategyOutcomeV2.id).filter(
                    StrategyOutcomeV2.decision_snapshot_id == RankingSnapshotItem.decision_snapshot_id,
                    StrategyOutcomeV2.outcome_population_id == population,
                    StrategyOutcomeV2.horizon_sessions == horizon,
                    StrategyOutcomeV2.evaluation_version == evaluator,
                ).exists()
                missing_conditions.append(and_(source_filter, ~exists_row))
        factor_fixed5 = db.query(StrategyOutcomeV2.id).filter(
            StrategyOutcomeV2.decision_snapshot_id == RankingSnapshotItem.decision_snapshot_id,
            StrategyOutcomeV2.outcome_population_id == POPULATION_SIGNAL_FORWARD_V2,
            StrategyOutcomeV2.horizon_sessions == 5,
            StrategyOutcomeV2.evaluation_version == forward_version,
        ).exists()
        missing_conditions.append(and_(
            source_filter,
            RankingSnapshotItem.outcome_population_id == POPULATION_SIGNAL_FORWARD_V2,
            ~factor_fixed5,
        ))
        for population, evaluator in (
            (RankingSnapshotItem.outcome_population_id, forward_version),
            (POPULATION_STRATEGY_EXECUTION_V2, execution_version),
        ):
            exists_primary = db.query(StrategyOutcomeV2.id).filter(
                StrategyOutcomeV2.decision_snapshot_id == RankingSnapshotItem.decision_snapshot_id,
                StrategyOutcomeV2.outcome_population_id == population,
                StrategyOutcomeV2.horizon_sessions == RankingSnapshotItem.primary_horizon_sessions,
                StrategyOutcomeV2.evaluation_version == evaluator,
            ).exists()
            missing_conditions.append(and_(
                source_filter,
                RankingSnapshotItem.primary_horizon_sessions.isnot(None),
                ~exists_primary,
            ))
    new_items = (
        db.query(RankingSnapshotItem)
        .filter(*base_filter, or_(*missing_conditions))
        .filter(~RankingSnapshotItem.decision_snapshot_id.in_(due_ids or ["__none__"]))
        .order_by(RankingSnapshotItem.session_date.asc(), RankingSnapshotItem.id.asc())
        .limit(remaining)
        .all()
    )
    return [_decision_snapshot(item) for item in [*ordered_due, *new_items]]


def _outcome_policy(population: str, horizon: int, decision: dict, calendar: dict,
                    *, evaluation_version: str, evaluation_revision: str | None) -> tuple[str, dict, str]:
    execution = population == POPULATION_STRATEGY_EXECUTION_V2
    policy_version = EXECUTION_POLICY_VERSION if execution else FORWARD_POLICY_VERSION
    try:
        from src.modules.strategy.backtest.cost_model import trading_lot
        lot = (trading_lot(decision["stock_market"])
               if execution and decision["stock_market"] == "TW" else None)
    except Exception:
        lot = None
    payload = {
        "population": population,
        "horizon_sessions": int(horizon),
        "evaluation_version": evaluation_version,
        "evaluation_revision": evaluation_revision or "baseline",
        "entry_rule": "limit-band-up-to-three-sessions" if execution else "next-session-close",
        "exit_rule": "overnight-gap-first-stop-target-else-hth-close" if execution else "hth-session-close",
        "same_bar_policy": "ambiguous-if-stop-and-target-touch" if execution else "not_applicable",
        "daily_validation_policy": "exact-session-final-ohlc-source-receipt-v1",
        "stock_price_basis_policy": "raw-unadjusted-price-reference-v1",
        "corporate_actions": "no numerical adjustment; price return is not total shareholder return",
        "calendar_validation": "covered-authoritative-calendar-only-v1",
        "maximum_observation_days": MAX_OBSERVATION_DAYS,
        "retry_backoff_hours": list(RETRY_BACKOFF_HOURS),
        "quantity_policy_version": _COST_POLICY_VERSION,
        "quantity_policy": ("one_configured_lot" if lot is not None else
                             "not_required_fixed_notional_return" if not execution else
                             "unavailable_without_market_lot_authority"),
        "cost_method": "dated_basis_points_per_side_on_entry_and_exit" if not execution
            else "dated_quantity_cashflows_on_entry_and_exit",
        "cost_denominator": "fixed_entry_notional" if not execution else "configured_quantity_cash_invested",
        "lot_size": lot,
        "cost_authority": "InstrumentCostAuthority effective on entry and exit sessions",
        "cost_unit": "percentage_points",
    }
    return policy_version, payload, sha256_json({"policy_version": policy_version, "payload": payload})


def _bars_for_decision(decision: dict, now: datetime, cache: dict[tuple[str, str], list]) -> list[dict]:
    market = str(decision["stock_market"] or "").upper()
    symbol = str(decision["stock_symbol"] or "").strip()
    key = (market, symbol)
    if key not in cache:
        code = _enum_market(market)
        if code is None or not symbol:
            cache[key] = []
        else:
            try:
                snap = date.fromisoformat(decision["session_date"])
                lookback = min(700, max(150, (now.date() - snap).days + 40))
                cache[key] = list(KlineCollector(code).get_klines(symbol, days=lookback) or [])
            except Exception as exc:
                logger.info("RC-D stock bars unavailable for %s %s: %s", market, symbol, exc)
                cache[key] = []
    return [normalize_bar(bar) for bar in cache[key]]


def _authority_payload(authority) -> dict[str, Any] | None:
    if authority is None:
        return None
    return {
        "instrument_id": authority.instrument_id,
        "tax_category": authority.tax_category,
        "classification_authority": authority.classification_authority,
        "classification_source": authority.classification_source,
        "policy_version": authority.policy_version,
        "effective_from": authority.effective_from,
        "effective_to": authority.effective_to,
        "stamp_duty_rate": authority.stamp_duty_rate,
        "commission_rate": authority.commission_rate,
        "minimum_commission": authority.minimum_commission,
        "transfer_fee_rate": authority.transfer_fee_rate,
        "slippage_bps": authority.slippage_bps,
        "evidence_hash": authority.evidence_hash,
    }


def _apply_costs(db, decision: dict, result: dict, *, population: str) -> dict[str, Any]:
    if result.get("entry_status") != "FILLED" or result.get("entry_price") is None or result.get("exit_price") is None:
        return {"quantity": None, "lot_size": None, "notional": None,
                "net_return_pct": None, "cost_assumption_pct": None,
                "reason_code": "stock_result_has_no_complete_round_trip",
                "breakdown": {"quantity_policy_version": _COST_POLICY_VERSION}}
    market = str(decision["stock_market"] or "").upper()
    instrument_id = str(decision["instrument_id"] or "")
    execution = population == POPULATION_STRATEGY_EXECUTION_V2
    if execution and market != "TW":
        return {"quantity": None, "lot_size": None, "notional": None,
                "net_return_pct": None, "cost_assumption_pct": None,
                "reason_code": "invalid_explicit_cost_policy",
                "breakdown": {"quantity_policy_version": _COST_POLICY_VERSION}}
    from src.modules.strategy.backtest.cost_model import (
        load_dated_cost_authority,
        resolve_dated_cost_model,
        trading_lot,
    )
    entry_date = result.get("entry_session_date")
    exit_date = result.get("exit_session_date")
    entry_authority = load_dated_cost_authority(db, instrument_id=instrument_id, session_date=entry_date)
    exit_authority = load_dated_cost_authority(db, instrument_id=instrument_id, session_date=exit_date)
    entry_resolution = resolve_dated_cost_model(
        instrument_id=instrument_id, session_date=entry_date, authority=entry_authority,
    )
    exit_resolution = resolve_dated_cost_model(
        instrument_id=instrument_id, session_date=exit_date, authority=exit_authority,
    )
    if not entry_resolution.net_available or not exit_resolution.net_available:
        reason = entry_resolution.reason_code or exit_resolution.reason_code or "cost_authority_missing"
        return {"quantity": None, "lot_size": None, "notional": None,
                "net_return_pct": None, "cost_assumption_pct": None,
                "reason_code": reason,
                "breakdown": {
                    "quantity_policy_version": _COST_POLICY_VERSION,
                    "entry_authority": entry_resolution.authority,
                    "exit_authority": exit_resolution.authority,
                }}
    if not execution:
        entry = entry_resolution.authority
        exit = exit_resolution.authority
        entry_bps = (
            float(entry["commission_rate"]) + float(entry["transfer_fee_rate"])
        ) * 10000.0 + float(entry["slippage_bps"])
        exit_bps = (
            float(exit["commission_rate"]) + float(exit["transfer_fee_rate"])
            + float(exit["stamp_duty_rate"])
        ) * 10000.0 + float(exit["slippage_bps"])
        cost_pct = (entry_bps + exit_bps) / 100.0
        gross = float(result["stock_gross_return_pct"])
        return {
            "quantity": None, "lot_size": None, "notional": None,
            "net_return_pct": round(gross - cost_pct, 4),
            "cost_assumption_pct": round(cost_pct, 4), "reason_code": "",
            "breakdown": {
                "quantity_policy_version": _COST_POLICY_VERSION,
                "quantity_policy": "not_required_fixed_notional_return",
                "cost_unit": "basis_points",
                "cost_method": "dated_basis_points_per_side_on_entry_and_exit",
                "cost_denominator": "fixed_entry_notional",
                "net_calculation": "gross_return_pct - (entry_cost_bps + exit_cost_bps) / 100",
                "entry_cost_bps": round(entry_bps, 6),
                "exit_cost_bps": round(exit_bps, 6),
                "minimum_commission_excluded": True,
                "entry_authority": entry_resolution.authority,
                "exit_authority": exit_resolution.authority,
            },
        }

    try:
        lot = trading_lot(market)
    except (TypeError, ValueError) as exc:
        return {"quantity": None, "lot_size": None, "notional": None,
                "net_return_pct": None, "cost_assumption_pct": None,
                "reason_code": "invalid_explicit_cost_policy",
                "breakdown": {"quantity_policy_version": _COST_POLICY_VERSION,
                              "policy_error": type(exc).__name__}}
    buy = entry_resolution.cost_model.fill("buy", float(result["entry_price"]), lot)
    sell = exit_resolution.cost_model.fill("sell", float(result["exit_price"]), lot)
    invested = -buy.cash_delta
    proceeds = sell.cash_delta
    pnl = proceeds - invested
    outcome = {
        "quantity": lot,
        "invested": invested,
        "proceeds": proceeds,
        "pnl": pnl,
        "pnl_pct": (pnl / invested * 100.0) if invested > 0 else None,
        "buy": buy,
        "sell": sell,
    }
    gross = float(result["stock_gross_return_pct"])
    if outcome["pnl_pct"] is None:
        return {"quantity": lot, "lot_size": lot,
                "notional": round(float(result["entry_price"]) * lot, 4),
                "net_return_pct": None, "cost_assumption_pct": None,
                "reason_code": "cost_invested_amount_invalid",
                "breakdown": {"quantity_policy_version": _COST_POLICY_VERSION}}
    net = float(outcome["pnl_pct"])
    return {
        "quantity": lot,
        "lot_size": lot,
        "notional": round(float(result["entry_price"]) * lot, 4),
        "net_return_pct": net,
        "cost_assumption_pct": round(gross - net, 4),
        "reason_code": "",
        "breakdown": {
            "quantity_policy_version": _COST_POLICY_VERSION,
            "quantity_policy": "one_configured_lot",
            "entry_authority": entry_resolution.authority,
            "exit_authority": exit_resolution.authority,
            "round_trip": {
                key: asdict(value) if hasattr(value, "__dataclass_fields__") else value
                for key, value in outcome.items()
            },
        },
    }


def _result_snapshot(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "stock_bars"}


def _retry_deadline(row: StrategyOutcomeV2, now: datetime) -> datetime:
    first = _aware(row.stock_first_attempt_at) or now
    return _aware(row.stock_retry_deadline_at) or first + timedelta(days=MAX_OBSERVATION_DAYS)


def _next_retry_time(result: dict, attempt_number: int, now: datetime,
                     calendar: dict, market: str) -> datetime:
    if result.get("evaluation_status") == "PENDING":
        sessions = result.get("expected_session_dates") or []
        policy = _session_policy(market)
        if policy:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(policy[0])
                for day in sessions:
                    local_close = datetime.combine(date.fromisoformat(day),
                                                   datetime.strptime(policy[1], "%H:%M").time(), tzinfo=tz)
                    planned = local_close.astimezone(timezone.utc)
                    if planned > now:
                        return planned
            except Exception:
                pass
    return now + retry_delay(attempt_number)


def _stock_result(decision: dict, population: str, horizon: int, bars: list[dict],
                  sessions: list[str], now: datetime, market_tz: str, close: str) -> dict:
    today = now.astimezone(MARKETS[_enum_market(decision["stock_market"])].get_tz()).date()
    if population == POPULATION_STRATEGY_EXECUTION_V2:
        return evaluate_execution_stock(
            bars=bars, expected_session_dates=sessions, horizon_sessions=horizon, today=today,
            action=decision.get("action") or "watch",
            is_holding_snapshot=bool(decision.get("is_holding_snapshot")),
            entry_low=decision.get("entry_low"), entry_high=decision.get("entry_high"),
            stop_loss=decision.get("stop_loss"), target_price=decision.get("target_price"),
            now=now, market_timezone=market_tz, session_close=close,
        )
    return evaluate_forward_stock(
        bars=bars, expected_session_dates=sessions, horizon_sessions=horizon, today=today,
        now=now, market_timezone=market_tz, session_close=close,
    )


def _append_stock_attempt(db, row: StrategyOutcomeV2, *, decision: dict,
                          calendar: dict, sessions: list[str], bars: list[dict],
                          result: dict, costs: dict, now: datetime) -> None:
    attempt_number = int(row.stock_attempt_count or 0) + 1
    first = _aware(row.stock_first_attempt_at) or now
    deadline = _retry_deadline(row, now)
    reason_code = str(result.get("reason_code") or "")
    retryable = (
        result.get("evaluation_status") in {"PENDING", "INSUFFICIENT_DATA"}
        and now < deadline
        and reason_code not in {"entry_window_invalid", "horizon_unsupported"}
    )
    reason = reason_code
    if now >= deadline:
        filled = row.entry_status == "FILLED" or result.get("entry_status") == "FILLED"
        result = {**result, "evaluation_status": "INSUFFICIENT_DATA",
                  "entry_status": "FILLED" if filled else "INSUFFICIENT_DATA",
                  "reason_code": "retry_deadline_exceeded"}
        costs = {"quantity": None, "lot_size": None, "notional": None,
                 "net_return_pct": None, "cost_assumption_pct": None,
                 "reason_code": "retry_deadline_exceeded",
                 "breakdown": {"quantity_policy_version": _COST_POLICY_VERSION}}
        retryable = False
        reason = "retry_deadline_exceeded"
    next_retry = _next_retry_time(result, attempt_number, now, calendar, row.stock_market) if retryable else None
    stock_bars = result.get("stock_bars") or [
        bar for bar in bars if str(bar.get("date") or "") in set(sessions)
    ]
    row.calendar_version = str(calendar.get("calendar_version") or "")
    row.calendar_source = str(calendar.get("source") or "")
    row.calendar_timezone = str(calendar.get("timezone") or "")
    row.calendar_confidence = str(calendar.get("confidence") or "UNKNOWN")
    stock_snapshot = {
        "schema": "panwatch.outcome-stock-attempt/v2",
        "attempted_at_utc": now.isoformat(),
        "decision": decision,
        "policy_version": row.policy_version,
        "policy_payload": row.policy_payload or {},
        "policy_hash": row.policy_hash,
        "calendar": calendar,
        "expected_session_dates": sessions,
        "stock_bars": stock_bars,
        "result": _result_snapshot(result),
        "cost": costs,
    }
    input_hash = sha256_json(stock_snapshot)
    if result.get("evaluation_status") in {"COMPLETE", "AMBIGUOUS"}:
        adjustment_modes = {str(bar.get("adjustment_mode") or "") for bar in stock_bars}
        row.stock_adjustment_mode = next(iter(adjustment_modes)) if len(adjustment_modes) == 1 else "mixed"
        row.expected_session_dates = list(sessions)
        row.entry_status = str(result.get("entry_status") or "INSUFFICIENT_DATA")
        row.evaluation_status = str(result.get("evaluation_status"))
        row.barrier_order = str(result.get("barrier_order") or "NONE")
        row.reason_code = reason
        row.entry_session_date = result.get("entry_session_date")
        row.exit_session_date = result.get("exit_session_date")
        row.entry_price = result.get("entry_price")
        row.exit_price = result.get("exit_price")
        row.entry_time_precision = str(result.get("entry_time_precision") or "SESSION_ONLY")
        row.stock_gross_return_pct = result.get("stock_gross_return_pct")
        row.stock_net_return_pct = costs.get("net_return_pct")
        row.stock_cost_assumption_pct = costs.get("cost_assumption_pct")
        row.quantity = costs.get("quantity")
        row.lot_size = costs.get("lot_size")
        row.notional = costs.get("notional")
        row.cost_reason_code = costs.get("reason_code") or ""
        row.cost_breakdown = costs.get("breakdown") or {}
        row.stock_input_hash = input_hash
        row.stock_retryable = False
        row.stock_next_retry_at = None
        row.stock_retry_deadline_at = _db_time(deadline)
        facts = {
            "identity": {key: getattr(row, key) for key in (
                "decision_snapshot_id", "candidate_id", "signal_run_id", "strategy_code",
                "stock_symbol", "stock_market", "instrument_id", "instrument_group",
                "instrument_authority", "session_date", "decision_at_utc", "decision_score",
                    "decision_capture_hash", "decision_payload", "decision_point_in_time_status",
                    "outcome_population_id",
                "horizon_sessions", "evaluation_version", "score_version", "policy_version",
                "policy_payload", "policy_hash", "calendar_version", "calendar_source",
                "calendar_timezone", "calendar_confidence", "expected_session_dates",
                "stock_adjustment_mode",
            )},
            "result": _result_snapshot(result),
            "cost": costs,
            "stock_input_hash": input_hash,
        }
        row.stock_facts_hash = sha256_json(facts)
    else:
        row.expected_session_dates = list(sessions)
        result_entry_status = str(result.get("entry_status") or "PENDING")
        row.entry_status = (
            "FILLED" if row.entry_status == "FILLED" or result_entry_status == "FILLED"
            else result_entry_status
        )
        row.evaluation_status = str(result.get("evaluation_status") or "INSUFFICIENT_DATA")
        row.reason_code = reason
        row.stock_input_hash = input_hash
        row.stock_retryable = retryable
        row.stock_next_retry_at = _db_time(next_retry)
        row.stock_retry_deadline_at = _db_time(deadline)
        if result.get("entry_session_date"):
            row.entry_session_date = result.get("entry_session_date")
            row.entry_price = result.get("entry_price")
            row.entry_time_precision = str(result.get("entry_time_precision") or "SESSION_ONLY")
    row.stock_attempt_count = attempt_number
    row.stock_first_attempt_at = _db_time(first)
    db.add(OutcomeEvaluationAttempt(
        outcome_id=row.id,
        attempt_kind="stock",
        attempt_number=attempt_number,
        input_hash=input_hash,
        input_snapshot=stock_snapshot,
        result_status=str(result.get("evaluation_status") or "INSUFFICIENT_DATA"),
        reason_code=reason,
        retryable=retryable,
        next_retry_at=_db_time(next_retry),
        created_at=_db_time(now),
    ))


def _benchmark_id(instrument_id: str, market: str) -> str | None:
    if market != "TW":
        return None
    venue = str(instrument_id or "").split(":", 1)[0]
    return {"TWSE": "TAIEX", "TPEX": "TPEX"}.get(venue)


def _benchmark_snapshot(read) -> dict:
    return {
        "benchmark_id": read.benchmark_id,
        "venue": read.venue,
        "provider": read.provider,
        "source_alias": read.source_alias,
        "source_contract": read.source_contract,
        "source_url": read.source_url,
        "timeframe": read.timeframe,
        "price_kind": read.price_kind,
        "adjustment_mode": read.adjustment_mode,
        "unit": read.unit,
        "basis": read.basis,
        "requested_start_date": read.requested_start_date,
        "requested_end_date": read.requested_end_date,
        "coverage_window": read.coverage_window,
        "coverage": [asdict(x) for x in read.coverage],
        "gaps": list(read.gaps),
        "bars": [{**asdict(x), "open": str(x.open), "high": str(x.high),
                  "low": str(x.low), "close": str(x.close)} for x in read.bars],
        "provenance": [asdict(x) for x in read.provenance],
        "provenance_total_count": read.provenance_total_count,
        "provenance_truncated": read.provenance_truncated,
        "served_at": read.served_at,
    }


def _benchmark_session_closed(session_date: str, now: datetime, market: str) -> bool | None:
    policy = _session_policy(market)
    if not policy:
        return None
    return _session_due(session_date, now=now, market_timezone=policy[0], session_close=policy[1])


def _benchmark_attempt_payload(row: StrategyOutcomeV2, now: datetime,
                               cache: dict[tuple[str, str, str], tuple[dict, str, str, bool]] | None = None,
                               sample_clock_after_fetch: bool = False,
                               ) -> tuple[dict, str, str, bool]:
    benchmark_id = _benchmark_id(row.instrument_id, row.stock_market)
    if not benchmark_id:
        return ({"reason_code": "benchmark_authority_unavailable", "benchmark_id": None},
                "UNAVAILABLE", "benchmark_authority_unavailable", False)
    if not row.entry_session_date or not row.exit_session_date:
        return ({"reason_code": "benchmark_dates_unavailable", "benchmark_id": benchmark_id},
                "UNAVAILABLE", "benchmark_dates_unavailable", False)
    if str(row.stock_adjustment_mode or "").strip().lower() not in {
        "raw", "raw_price", "unadjusted", "none",
    }:
        return ({"reason_code": "benchmark_basis_incomparable", "benchmark_id": benchmark_id,
                 "stock_adjustment_mode": row.stock_adjustment_mode or "unknown",
                 "benchmark_basis": "raw_price_index"},
                "UNAVAILABLE", "benchmark_basis_incomparable", False)
    request_key = (benchmark_id, str(row.entry_session_date), str(row.exit_session_date))
    if cache is not None and request_key in cache:
        return cache[request_key]
    try:
        read = get_market_data().benchmark_bars(
            benchmark_id,
            start_date=row.entry_session_date,
            end_date=row.exit_session_date,
            limit=120,
            timeout_sec=8,
        )
        validation_clock = utc_now() if sample_clock_after_fetch else now
        validation_clock = validation_clock.astimezone(timezone.utc)
        snapshot = _benchmark_snapshot(read)
        expected_venue = "TWSE" if benchmark_id == "TAIEX" else "TPEX"
        expected_provider = "TWSE" if benchmark_id == "TAIEX" else "TPEx"
        expected_alias = "MI_5MINS_HIST" if benchmark_id == "TAIEX" else "tpex_index"
        if (
            read.benchmark_id != benchmark_id or read.venue != expected_venue
            or read.provider != expected_provider or read.source_alias != expected_alias
            or read.timeframe != "day" or read.price_kind != "benchmark_index"
            or read.adjustment_mode != "raw_price_index" or read.unit != "index_points"
            or read.basis != "raw_price_index"
            or read.requested_start_date != row.entry_session_date
            or read.requested_end_date != row.exit_session_date
            or read.coverage_window.get("start_date") != row.entry_session_date
            or read.coverage_window.get("end_date") != row.exit_session_date
            or not read.source_contract or not read.source_url
        ):
            raise ValueError("benchmark_contract_mismatch")
        served_at = _timestamp(read.served_at)
        if served_at is None or served_at.astimezone(timezone.utc) > validation_clock:
            raise ValueError("benchmark_served_at_from_future")
        if read.provenance_truncated or read.provenance_total_count != len(read.provenance):
            raise ValueError("benchmark_provenance_incomplete")
        for capture in read.provenance:
            captured_at = _timestamp(capture.captured_at)
            if captured_at is None or captured_at.astimezone(timezone.utc) > validation_clock:
                raise ValueError("benchmark_provenance_from_future")
            if (capture.benchmark_id != benchmark_id or capture.source_contract != read.source_contract
                    or capture.source_alias != expected_alias or capture.source_url != read.source_url
                    or not capture.payload_sha256 or int(capture.record_count or 0) <= 0):
                raise ValueError("benchmark_provenance_identity_mismatch")
        coverage = list(read.coverage)
        coverage_by_date = {day.trade_date: day.status for day in coverage}
        if len(coverage_by_date) != len(coverage):
            raise ValueError("benchmark_coverage_duplicate_conflict")
        provenance_by_capture = {item.capture_id: item for item in read.provenance}
        if len(provenance_by_capture) != len(read.provenance):
            raise ValueError("benchmark_provenance_duplicate_conflict")
        by_date: dict[str, Any] = {}
        for bar in read.bars:
            previous = by_date.get(bar.trade_date)
            if previous is not None and previous != bar:
                raise ValueError("benchmark_bar_duplicate_conflict")
            if (
                bar.benchmark_id != benchmark_id or bar.unit != "index_points"
                or bar.basis != "raw_price_index" or bar.source_contract != read.source_contract
                or bar.source_alias != expected_alias or bar.source_url is None
                or bar.capture_id not in provenance_by_capture
                or bar.payload_sha256 != provenance_by_capture[bar.capture_id].payload_sha256
                or bar.captured_at != provenance_by_capture[bar.capture_id].captured_at
                or bar.request_scope != provenance_by_capture[bar.capture_id].request_scope
            ):
                raise ValueError("benchmark_bar_provenance_mismatch")
            try:
                values = [Decimal(str(getattr(bar, field))) for field in ("open", "high", "low", "close")]
            except Exception as exc:
                raise ValueError("benchmark_bar_ohlc_invalid") from exc
            if any(not value.is_finite() or value <= 0 or not math.isfinite(float(value))
                   for value in values):
                raise ValueError("benchmark_bar_ohlc_invalid")
            opened, high, low, close = values
            if high < low or not low <= opened <= high or not low <= close <= high:
                raise ValueError("benchmark_bar_ohlc_invalid")
            captured_at = _timestamp(bar.captured_at)
            if (captured_at is None or captured_at.astimezone(timezone.utc) > validation_clock
                    or _benchmark_session_closed(bar.trade_date, captured_at, row.stock_market) is not True):
                raise ValueError("benchmark_bar_not_finalized")
            by_date[bar.trade_date] = bar
        required_dates = [
            day for day in (row.expected_session_dates or [])
            if str(row.entry_session_date) <= str(day) <= str(row.exit_session_date)
        ]
        if not required_dates:
            required_dates = [str(row.entry_session_date), str(row.exit_session_date)]
        if any(_benchmark_session_closed(day, validation_clock, row.stock_market) is not True
               for day in required_dates):
            result = (snapshot, "PENDING", "benchmark_session_not_closed", True)
            if cache is not None:
                cache[request_key] = result
            return result
        if any(day not in coverage_by_date or coverage_by_date[day] != "AVAILABLE" for day in required_dates):
            result = (snapshot, "UNAVAILABLE", "venue_benchmark_date_missing", True)
            if cache is not None:
                cache[request_key] = result
            return result
        entry, exit = by_date.get(row.entry_session_date), by_date.get(row.exit_session_date)
        if entry is None or exit is None:
            result = (snapshot, "UNAVAILABLE", "venue_benchmark_date_missing", True)
            if cache is not None:
                cache[request_key] = result
            return result
        result = ({**snapshot, "entry_capture_id": entry.capture_id, "exit_capture_id": exit.capture_id},
                  "AVAILABLE", "", False)
        if cache is not None:
            cache[request_key] = result
        return result
    except Exception as exc:
        validation_clock = utc_now() if sample_clock_after_fetch else now
        reason = getattr(exc, "reason_code", "benchmark_source_unavailable") or "benchmark_source_unavailable"
        if isinstance(exc, ValueError) and str(exc).startswith("benchmark_"):
            status = "UNAVAILABLE"
            reason = str(exc)
        else:
            status = "PENDING"
        result = ({"benchmark_id": benchmark_id, "error_type": type(exc).__name__,
                   "reason_code": reason, "attempted_at_utc": validation_clock.isoformat()},
                  status, str(reason), status != "UNAVAILABLE")
        if cache is not None:
            cache[request_key] = result
        return result


def _append_benchmark_attempt(db, row: StrategyOutcomeV2, *, snapshot: dict,
                              status: str, reason: str, retryable: bool,
                              now: datetime) -> None:
    attempt_number = int(row.benchmark_attempt_count or 0) + 1
    first = _aware(row.benchmark_retry_deadline_at) or now + timedelta(days=MAX_OBSERVATION_DAYS)
    deadline = first
    if now >= deadline:
        status, reason = "UNAVAILABLE", "benchmark_retry_deadline_exceeded"
        snapshot = {**snapshot, "reason_code": reason}
    retryable = bool(retryable and now < deadline and status in {"PENDING", "UNAVAILABLE"})
    next_retry = now + retry_delay(attempt_number) if retryable else None
    input_hash = sha256_json({"attempted_at_utc": now.isoformat(), "snapshot": snapshot})
    row.benchmark_status = status
    row.benchmark_reason_code = reason
    row.benchmark_attempt_count = attempt_number
    row.benchmark_retryable = retryable
    row.benchmark_retry_deadline_at = _db_time(deadline)
    row.benchmark_next_retry_at = _db_time(next_retry)
    if status == "AVAILABLE":
        by_date = {item["trade_date"]: item for item in snapshot.get("bars", [])}
        entry = by_date.get(row.entry_session_date) or {}
        exit = by_date.get(row.exit_session_date) or {}
        entry_close = float(Decimal(str(entry["close"])))
        exit_close = float(Decimal(str(exit["close"])))
        row.benchmark_id = snapshot.get("benchmark_id")
        row.benchmark_entry_close = entry_close
        row.benchmark_exit_close = exit_close
        row.benchmark_gross_return_pct = (exit_close / entry_close - 1.0) * 100.0
        comparable_stock_basis = str(row.stock_adjustment_mode or "").strip().lower() in {
            "raw", "raw_price", "unadjusted", "none",
        }
        row.relative_net_percentage_points = (
            float(row.stock_net_return_pct) - row.benchmark_gross_return_pct
            if row.stock_net_return_pct is not None and comparable_stock_basis else None
        )
        if not comparable_stock_basis:
            row.benchmark_reason_code = "benchmark_basis_incomparable"
    else:
        row.benchmark_id = snapshot.get("benchmark_id")
    db.add(OutcomeEvaluationAttempt(
        outcome_id=row.id, attempt_kind="benchmark", attempt_number=attempt_number,
        input_hash=input_hash, input_snapshot=snapshot, result_status=status,
        reason_code=reason, retryable=retryable, next_retry_at=_db_time(next_retry),
        created_at=_db_time(now),
    ))


def _new_outcome(db, decision: dict, population: str, horizon: int,
                 evaluation_version: str, policy_version: str, policy_payload: dict,
                 policy_hash: str, calendar: dict) -> StrategyOutcomeV2:
    row = StrategyOutcomeV2(
        decision_snapshot_id=decision["decision_snapshot_id"],
        candidate_id=decision.get("candidate_id"),
        signal_run_id=decision.get("signal_run_id"),
        strategy_code=decision.get("strategy_code") or "",
        stock_symbol=decision.get("stock_symbol") or "",
        stock_market=decision.get("stock_market") or "",
        instrument_id=decision.get("instrument_id") or "",
        instrument_group=decision.get("instrument_group") or "UNKNOWN",
        instrument_authority=decision.get("instrument_authority") or {},
        session_date=decision.get("session_date") or "",
        decision_at_utc=_db_time(datetime.fromisoformat(decision["decision_at_utc"]))
            if decision.get("decision_at_utc") else None,
        decision_score=decision.get("ranking_value"),
        decision_capture_hash=decision.get("capture_hash") or "",
        decision_payload={
            "ranking_value": decision.get("ranking_value"),
            "score_version": decision.get("score_version") or "",
            "action": decision.get("action") or "watch",
            "is_holding_snapshot": bool(decision.get("is_holding_snapshot")),
            "candidate_source": decision.get("candidate_source") or "",
            "entry_low": decision.get("entry_low"),
            "entry_high": decision.get("entry_high"),
            "stop_loss": decision.get("stop_loss"),
            "target_price": decision.get("target_price"),
            "point_in_time_status": decision.get("point_in_time_status") or "UNVERIFIABLE",
        },
        decision_point_in_time_status=decision.get("point_in_time_status") or "UNVERIFIABLE",
        outcome_population_id=population,
        horizon_sessions=horizon,
        evaluation_version=evaluation_version,
        score_version=decision.get("score_version") or "",
        policy_version=policy_version,
        policy_payload=policy_payload,
        policy_hash=policy_hash,
        calendar_version=str(calendar.get("calendar_version") or ""),
        calendar_source=str(calendar.get("source") or ""),
        calendar_timezone=str(calendar.get("timezone") or ""),
        calendar_confidence=str(calendar.get("confidence") or "UNKNOWN"),
    )
    db.add(row)
    db.flush()
    return row


def evaluate_point_in_time_outcomes(*, horizons: tuple[int, ...] = DEFAULT_HORIZONS,
                                    snapshot_days: int = 180, limit: int = 1200,
                                    now: datetime | None = None,
                                    evaluation_revision: str | None = None) -> dict[str, Any]:
    """Evaluate sealed signal/candidate decisions and their separate execution cohorts.

    Each stock attempt commits its immutable input snapshot and projection before
    benchmark acquisition starts. Benchmark retries update benchmark columns only.
    """
    fixed_clock = now is not None
    clock = now or utc_now()
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("RC-D evaluator requires an aware fixed clock")
    revision = str(evaluation_revision or "").strip() or None
    if revision is not None and not _REVISION_PATTERN.fullmatch(revision):
        raise ValueError("evaluation_revision must match [A-Za-z0-9][A-Za-z0-9._-]{0,47}")
    clock = clock.astimezone(timezone.utc)
    result = {"decisions": 0, "created": 0, "attempted": 0, "completed": 0,
              "pending": 0, "insufficient": 0, "ambiguous": 0,
              "not_filled": 0, "benchmarks_attempted": 0, "skipped_not_due": 0,
              "evaluation_revision": revision or "baseline"}
    if not _RUN_LOCK.acquire(blocking=False):
        return {**result, "skipped_running": True}
    try:
        db = SessionLocal()
        try:
            cutoff = (clock.date() - timedelta(days=max(1, min(int(snapshot_days), 365)))).isoformat()
            requested_horizons = {
                max(1, int(value)) for value in horizons if int(value) > 0
            }
            decisions = _decision_queue(
                db, cutoff=cutoff, clock=clock, limit=max(1, int(limit)),
                horizons=requested_horizons, evaluation_revision=revision,
            )
        finally:
            db.close()
        result["decisions"] = len(decisions)
        bars_cache: dict[tuple[str, str], list] = {}
        benchmark_cache: dict[tuple[str, str, str], tuple[dict, str, str, bool]] = {}
        for decision in decisions:
            input_population = decision["population"]
            if input_population not in _SUPPORTED_POPULATIONS:
                continue
            execution_horizons = set(requested_horizons)
            try:
                primary_horizon = int(decision.get("primary_horizon_sessions") or 0)
                if primary_horizon > 0:
                    execution_horizons.add(primary_horizon)
            except (TypeError, ValueError):
                pass
            forward_horizons = set(execution_horizons)
            if input_population == POPULATION_SIGNAL_FORWARD_V2:
                forward_horizons.add(5)
            forward_evaluator = _revision_evaluator(decision["evaluation_version"], revision)
            execution_evaluator = _revision_evaluator(EVALUATOR_EXECUTION_V2, revision)
            work_keys = [
                *((input_population, horizon, forward_evaluator) for horizon in sorted(forward_horizons)),
                *((POPULATION_STRATEGY_EXECUTION_V2, horizon, execution_evaluator)
                  for horizon in sorted(execution_horizons)),
            ]
            for population, horizon, evaluator in work_keys:
                needed = horizon + 1 if population != POPULATION_STRATEGY_EXECUTION_V2 else 3 + horizon
                if horizon > _MAX_SUPPORTED_HORIZON_SESSIONS:
                    sessions, calendar = [], {
                        "status": "unknown", "reason_code": "horizon_unsupported",
                        "requested_horizon_sessions": horizon,
                        "maximum_supported_horizon_sessions": _MAX_SUPPORTED_HORIZON_SESSIONS,
                    }
                else:
                    sessions, calendar = _expected_sessions(
                        decision["stock_market"], decision["session_date"], needed,
                    )
                market_policy = _session_policy(decision["stock_market"])
                if market_policy is None:
                    calendar = {**calendar, "status": "unknown", "reason_code": "market_timezone_missing"}
                    sessions = []
                version, policy, policy_hash = _outcome_policy(
                    population, horizon, decision, calendar,
                    evaluation_version=evaluator, evaluation_revision=revision,
                )
                if population == POPULATION_STRATEGY_EXECUTION_V2:
                    version = EXECUTION_POLICY_VERSION
                deadline_expired = False
                deadline: datetime | None = None
                with SessionLocal() as db:
                    row = db.query(StrategyOutcomeV2).filter_by(
                        decision_snapshot_id=decision["decision_snapshot_id"],
                        outcome_population_id=population,
                        horizon_sessions=horizon,
                        evaluation_version=evaluator,
                    ).first()
                    created = row is None
                    if created:
                        row = _new_outcome(
                            db, decision, population, horizon, evaluator,
                            version, policy, policy_hash, calendar,
                        )
                        db.commit()
                        result["created"] += 1
                    else:
                        due = _aware(row.stock_next_retry_at)
                        deadline = _aware(row.stock_retry_deadline_at)
                        if row.stock_retryable is False or (
                            due is not None and due > clock
                            and (deadline is None or deadline > clock)
                        ):
                            result["skipped_not_due"] += 1
                            benchmark_due = _aware(row.benchmark_next_retry_at)
                            benchmark_deadline = _aware(row.benchmark_retry_deadline_at)
                            if row.evaluation_status in {"COMPLETE", "AMBIGUOUS"}:
                                if row.benchmark_retryable and (
                                    benchmark_due is None or benchmark_due <= clock
                                    or (benchmark_deadline is not None and benchmark_deadline <= clock)
                                ):
                                    result["benchmarks_attempted"] += 1
                                    snapshot, status, reason, retryable = _benchmark_attempt_payload(
                                        row, clock, benchmark_cache,
                                        sample_clock_after_fetch=not fixed_clock,
                                    )
                                    attempt_clock = clock if fixed_clock else utc_now().astimezone(timezone.utc)
                                    _append_benchmark_attempt(
                                        db, row, snapshot=snapshot, status=status,
                                        reason=reason, retryable=retryable, now=attempt_clock,
                                    )
                                    db.commit()
                            continue
                        deadline_expired = deadline is not None and clock >= deadline
                execution_preflight = None
                if population == POPULATION_STRATEGY_EXECUTION_V2:
                    preflight = evaluate_execution_stock(
                        bars=[], expected_session_dates=sessions,
                        horizon_sessions=horizon, today=clock.date(),
                        action=decision.get("action") or "watch",
                        is_holding_snapshot=bool(decision.get("is_holding_snapshot")),
                        entry_low=decision.get("entry_low"),
                        entry_high=decision.get("entry_high"),
                        stop_loss=decision.get("stop_loss"),
                        target_price=decision.get("target_price"), now=clock,
                        market_timezone=market_policy[0] if market_policy else "UTC",
                        session_close=market_policy[1] if market_policy else "00:00",
                    )
                    if preflight.get("reason_code") in {
                        "order_not_applicable", "entry_window_invalid",
                    }:
                        execution_preflight = preflight
                bars = (
                    _bars_for_decision(decision, clock, bars_cache)
                    if calendar.get("status") == "known" and not deadline_expired
                    and execution_preflight is None else []
                )
                attempt_clock = clock if fixed_clock else utc_now().astimezone(timezone.utc)
                deadline_expired = deadline_expired or (
                    deadline is not None and attempt_clock >= deadline
                )
                if execution_preflight is not None:
                    stock_result = execution_preflight
                elif deadline_expired:
                    stock_result = {
                        "evaluation_status": "INSUFFICIENT_DATA",
                        "entry_status": "INSUFFICIENT_DATA",
                        "reason_code": "retry_deadline_exceeded",
                        "expected_session_dates": sessions,
                    }
                elif calendar.get("status") != "known":
                    stock_result = {
                        "evaluation_status": "INSUFFICIENT_DATA",
                        "entry_status": "INSUFFICIENT_DATA",
                        "reason_code": calendar.get("reason_code") or "calendar_unverifiable",
                        "expected_session_dates": sessions,
                    }
                elif not market_policy:
                    stock_result = {"evaluation_status": "INSUFFICIENT_DATA",
                                    "entry_status": "INSUFFICIENT_DATA",
                                    "reason_code": "calendar_unverifiable",
                                    "expected_session_dates": sessions}
                else:
                    stock_result = _stock_result(
                        decision, population, horizon, bars, sessions, attempt_clock,
                        market_policy[0], market_policy[1],
                    )
                with SessionLocal() as db:
                    row = db.query(StrategyOutcomeV2).filter_by(
                        decision_snapshot_id=decision["decision_snapshot_id"],
                        outcome_population_id=population,
                        horizon_sessions=horizon,
                        evaluation_version=evaluator,
                    ).first()
                    if row is None:
                        row = _new_outcome(db, decision, population, horizon, evaluator,
                                           version, policy, policy_hash, calendar)
                        result["created"] += 1
                    if row.stock_retryable is False or row.evaluation_status in {"COMPLETE", "AMBIGUOUS"}:
                        db.rollback()
                        continue
                    cost = _apply_costs(db, decision, stock_result, population=population)
                    _append_stock_attempt(
                        db, row, decision=decision, calendar=calendar,
                        sessions=sessions, bars=bars, result=stock_result,
                        costs=cost, now=attempt_clock,
                    )
                    if calendar.get("status") == "known" and not row.calendar_version:
                        row.calendar_version = str(calendar.get("calendar_version") or "")
                    db.commit()
                    result["attempted"] += 1
                    if row.evaluation_status == "COMPLETE":
                        result["completed"] += 1
                        if row.entry_status == "NOT_FILLED":
                            result["not_filled"] += 1
                    elif row.evaluation_status == "AMBIGUOUS":
                        result["ambiguous"] += 1
                    elif row.evaluation_status == "PENDING":
                        result["pending"] += 1
                    else:
                        result["insufficient"] += 1
                    if row.evaluation_status in {"COMPLETE", "AMBIGUOUS"} and row.benchmark_retryable:
                        snapshot, status, reason, retryable = _benchmark_attempt_payload(
                            row, attempt_clock, benchmark_cache,
                            sample_clock_after_fetch=not fixed_clock,
                        )
                        benchmark_attempt_clock = (
                            attempt_clock if fixed_clock else utc_now().astimezone(timezone.utc)
                        )
                        _append_benchmark_attempt(
                            db, row, snapshot=snapshot, status=status,
                            reason=reason, retryable=retryable, now=benchmark_attempt_clock,
                        )
                        db.commit()
                        result["benchmarks_attempted"] += 1
        return {**result, "committed": True}
    except Exception as exc:
        logger.exception("RC-D point-in-time outcome evaluation failed: %s", exc)
        result["error"] = str(exc)
        return result
    finally:
        _RUN_LOCK.release()


def point_in_time_outcome_stats(*, days: int = 45, db=None) -> dict[str, Any]:
    """Return explicitly partitioned v2 counts and gross/net/relative denominators."""
    own_db = db is None
    db = db or SessionLocal()
    try:
        since = utc_now() - timedelta(days=max(1, min(int(days or 45), 365)))
        since_day = since.date().isoformat()
        rows = db.query(StrategyOutcomeV2).filter(StrategyOutcomeV2.session_date >= since_day).all()
        partitions: dict[tuple, dict] = {}
        for row in rows:
            key = (row.stock_market or "UNKNOWN", row.outcome_population_id or "UNKNOWN",
                   int(row.horizon_sessions or 0), row.evaluation_version or "UNKNOWN",
                   row.score_version or "UNKNOWN")
            stats = partitions.setdefault(key, {
                "market": key[0], "population": key[1], "horizon_sessions": key[2],
                "evaluator_version": key[3], "score_version": key[4],
                "raw_outcome_rows": 0, "decision_dates": set(), "all_units": set(),
                "gross_by_unit": {}, "net_by_unit": {}, "relative_by_unit": {},
                "benchmark_by_unit": {}, "benchmark_gross_by_unit": {},
                "gross_row_denominator": 0, "net_row_denominator": 0,
                "relative_row_denominator": 0, "benchmark_gross_row_denominator": 0,
                "benchmark_available_rows": 0, "benchmark_pending_rows": 0,
                "benchmark_unavailable_rows": 0,
                "entry_count": 0,
                "not_filled": 0, "ambiguous": 0, "insufficient_data": 0,
                "pending": 0, "complete": 0,
            })
            instrument = str(row.instrument_id or f"{row.stock_market}:{row.stock_symbol}")
            unit = (instrument, str(row.session_date or ""), str(row.strategy_code or ""))
            stats["raw_outcome_rows"] += 1
            stats["decision_dates"].add(row.session_date)
            stats["all_units"].add(unit)
            if row.entry_status == "FILLED":
                stats["entry_count"] += 1
            if row.evaluation_status == "COMPLETE":
                stats["complete"] += 1
            if row.entry_status == "NOT_FILLED":
                stats["not_filled"] += 1
            if row.evaluation_status == "AMBIGUOUS":
                stats["ambiguous"] += 1
            if row.evaluation_status == "INSUFFICIENT_DATA":
                stats["insufficient_data"] += 1
            if row.evaluation_status == "PENDING":
                stats["pending"] += 1
            if row.stock_gross_return_pct is not None:
                stats["gross_row_denominator"] += 1
                stats["gross_by_unit"].setdefault(unit, []).append(float(row.stock_gross_return_pct))
            if row.stock_net_return_pct is not None:
                stats["net_row_denominator"] += 1
                stats["net_by_unit"].setdefault(unit, []).append(float(row.stock_net_return_pct))
            if row.relative_net_percentage_points is not None:
                stats["relative_row_denominator"] += 1
                stats["relative_by_unit"].setdefault(unit, []).append(
                    float(row.relative_net_percentage_points)
                )
            benchmark_status = str(row.benchmark_status or "UNAVAILABLE").upper()
            if benchmark_status == "AVAILABLE":
                stats["benchmark_available_rows"] += 1
            elif benchmark_status == "PENDING":
                stats["benchmark_pending_rows"] += 1
            else:
                stats["benchmark_unavailable_rows"] += 1
            stats["benchmark_by_unit"].setdefault(unit, []).append(benchmark_status)
            if row.benchmark_gross_return_pct is not None:
                stats["benchmark_gross_row_denominator"] += 1
                stats["benchmark_gross_by_unit"].setdefault(unit, []).append(
                    float(row.benchmark_gross_return_pct)
                )
        out = []
        for stats in partitions.values():
            stats["decision_date_count"] = len(stats.pop("decision_dates"))
            stats["distinct_units"] = len(stats.pop("all_units"))
            stats["unit_definition"] = "distinct instrument_id + decision session_date + strategy_code"
            for prefix, mapping_key, row_key in (
                ("gross", "gross_by_unit", "gross_row_denominator"),
                ("net", "net_by_unit", "net_row_denominator"),
                ("relative", "relative_by_unit", "relative_row_denominator"),
                ("benchmark_gross", "benchmark_gross_by_unit", "benchmark_gross_row_denominator"),
            ):
                mapping = stats.pop(mapping_key)
                values = [sum(unit_values) / len(unit_values) for unit_values in mapping.values()]
                denominator = len(values)
                stats[f"{prefix}_row_denominator"] = stats.pop(row_key)
                stats[f"{prefix}_denominator_units"] = denominator
                stats[f"{prefix}_units"] = denominator
                stats[f"{prefix}_win_rate_pct"] = (
                    round(sum(value > 0 for value in values) / denominator * 100, 2)
                    if denominator else None
                )
                stats[f"{prefix}_mean_return_pct"] = (
                    round(sum(values) / denominator, 4) if denominator else None
                )
            benchmark_units = stats.pop("benchmark_by_unit")
            stats["benchmark_availability_denominator_units"] = sum(
                any(status != "PENDING" for status in values)
                for values in benchmark_units.values()
            )
            stats["benchmark_available_units"] = sum(
                any(status == "AVAILABLE" for status in values)
                for values in benchmark_units.values()
            )
            stats["benchmark_pending_units"] = sum(
                all(status == "PENDING" for status in values)
                for values in benchmark_units.values()
            )
            stats["benchmark_unavailable_units"] = sum(
                any(status == "UNAVAILABLE" for status in values)
                for values in benchmark_units.values()
            )
            stats["gross_unit"] = "percent per distinct instrument/date/strategy"
            stats["net_unit"] = "percent per distinct instrument/date/strategy"
            stats["relative_unit"] = "percentage_points per distinct instrument/date/strategy"
            stats["benchmark_gross_unit"] = "percent per distinct instrument/date/strategy"
            out.append(stats)
        out.sort(key=lambda x: (x["market"], x["population"], x["horizon_sessions"],
                               x["evaluator_version"], x["score_version"]))
        return {
            "basis": "versioned point-in-time outcomes; equal-weighted means and win rates use distinct instrument/date/strategy units; each metric reports row and unit denominators",
            "window_days": int(days),
            "partitions": out,
        }
    finally:
        if own_db:
            db.close()
