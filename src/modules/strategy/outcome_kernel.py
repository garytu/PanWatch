"""Pure, versioned outcome rules for session-forward and simulated execution results."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


POPULATION_SIGNAL_FORWARD_V2 = "signal-forward-v2"
POPULATION_CANDIDATE_FORWARD_V2 = "candidate-forward-v2"
POPULATION_STRATEGY_EXECUTION_V2 = "strategy-execution-v2"

EVALUATOR_SIGNAL_FORWARD_V2 = "signal-session-evaluator-v2.1"
EVALUATOR_CANDIDATE_FORWARD_V2 = "candidate-session-evaluator-v2.1"
EVALUATOR_EXECUTION_V2 = "strategy-execution-evaluator-v2.1"
SCORE_VERSION_SIGNAL_V1 = "ranker-v1"
SCORE_VERSION_CANDIDATE_V1 = "candidate-score-v1"

FORWARD_POLICY_VERSION = "forward-next-session-close-h-v2.1"
EXECUTION_POLICY_VERSION = "long-limit-three-sessions-gap-first-v2.1"
RETRY_BACKOFF_HOURS = (1, 6, 24)
MAX_OBSERVATION_DAYS = 30


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str, allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


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
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def normalize_bar(bar: Any) -> dict[str, Any]:
    """Return a JSON-safe bar capture without collapsing intraday timestamps to dates."""
    if isinstance(bar, Mapping):
        get = bar.get
    else:
        get = lambda key, default=None: getattr(bar, key, default)
    return {
        "date": str(get("date", "")),
        "open": get("open"), "high": get("high"), "low": get("low"),
        "close": get("close"), "volume": get("volume", 0),
        "provider": get("provider"), "source_contract": get("source_contract"),
        "adjustment_mode": get("adjustment_mode"),
        "content_sha256": get("content_sha256") or get("payload_sha256"),
        "capture_id": get("capture_id"),
        "receipt_at_utc": get("receipt_at_utc") or get("captured_at"),
        "source_available_at_utc": get("source_available_at_utc"),
        "timeframe": get("timeframe"), "volume_unit": get("volume_unit"),
        "final": get("final"),
    }


def _valid_session(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _session_due(session_date: str, *, now: datetime, market_timezone: str,
                 session_close: str) -> bool | None:
    if not _valid_session(session_date) or now.tzinfo is None or now.utcoffset() is None:
        return None
    try:
        zone = ZoneInfo(market_timezone)
        hour, minute = (int(part) for part in session_close.split(":", 1))
        close = datetime.combine(date.fromisoformat(session_date), time(hour, minute), tzinfo=zone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None
    return now.astimezone(timezone.utc) >= close.astimezone(timezone.utc)


def _content_identity(bar: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "date": bar["date"],
        "open": float(bar["open"]), "high": float(bar["high"]),
        "low": float(bar["low"]), "close": float(bar["close"]),
        "volume": float(bar.get("volume", 0) or 0),
        "provider": bar.get("provider"),
        "adjustment_mode": bar.get("adjustment_mode"),
        "volume_unit": bar.get("volume_unit"),
        "timeframe": bar.get("timeframe"),
    }


def bar_validation_reason(bar: Mapping[str, Any], *, session_date: str,
                          today: date, now: datetime | None = None,
                          market_timezone: str = "Asia/Taipei",
                          session_close: str = "13:30") -> str:
    """Validate one daily observation and the receipt evidence that its session closed."""
    if bar.get("date") != session_date or not _valid_session(session_date):
        return "daily_timeframe_required"
    values = [_number(bar.get(name)) for name in ("open", "high", "low", "close")]
    if any(value is None or value <= 0 for value in values):
        return "stock_window_invalid"
    opened, high, low, close = values
    if high < low or not low <= opened <= high or not low <= close <= high:
        return "stock_window_invalid"
    if str(bar.get("timeframe") or "").lower() not in {"day", "daily", "1d"}:
        return "daily_timeframe_required"
    provider = str(bar.get("provider") or "").strip()
    contract = str(bar.get("source_contract") or "").strip()
    if not provider or not contract:
        return "bar_source_contract_missing"
    adjustment_mode = str(bar.get("adjustment_mode") or "").strip().lower()
    if not adjustment_mode:
        return "bar_adjustment_identity_missing"
    if adjustment_mode not in {"raw", "raw_price", "unadjusted", "none"}:
        return "stock_adjustment_basis_unsupported"
    if not str(bar.get("content_sha256") or "").strip() or not str(bar.get("capture_id") or "").strip():
        return "bar_content_identity_missing"
    try:
        if sha256_json(_content_identity(bar)) != str(bar.get("content_sha256")):
            return "bar_content_identity_mismatch"
    except (TypeError, ValueError, OverflowError, KeyError):
        return "stock_window_invalid"
    receipt = _timestamp(bar.get("receipt_at_utc"))
    if receipt is None:
        return "bar_receipt_time_missing" if not bar.get("receipt_at_utc") else "bar_receipt_time_invalid"
    available = bar.get("source_available_at_utc")
    available_at = _timestamp(available) if available not in (None, "") else None
    if available not in (None, "") and available_at is None:
        return "bar_source_time_invalid"
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None or clock.utcoffset() is None:
        return "evaluation_clock_invalid"
    clock_utc = clock.astimezone(timezone.utc)
    if receipt.astimezone(timezone.utc) > clock_utc:
        return "bar_receipt_from_future"
    if available_at is not None and (
        available_at.astimezone(timezone.utc) > clock_utc
        or available_at.astimezone(timezone.utc) > receipt.astimezone(timezone.utc)
    ):
        return "bar_source_time_from_future"
    due = _session_due(session_date, now=clock, market_timezone=market_timezone,
                       session_close=session_close)
    if due is None:
        return "calendar_unverifiable"
    if not due:
        return "daily_bar_not_final"
    if bar.get("final") is False:
        return "daily_bar_unfinished"
    try:
        local_receipt = receipt.astimezone(ZoneInfo(market_timezone))
        close_hour, close_minute = (int(part) for part in session_close.split(":", 1))
        close_at = datetime.combine(date.fromisoformat(session_date), time(close_hour, close_minute),
                                    tzinfo=ZoneInfo(market_timezone))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return "calendar_unverifiable"
    if available_at is not None and available_at.astimezone(ZoneInfo(market_timezone)) < close_at:
        return "daily_bar_unfinished"
    if local_receipt < close_at:
        return "daily_bar_unfinished"
    return ""


def _validated_window(bars: Sequence[Mapping[str, Any]], session_dates: Sequence[str],
                      *, today: date, now: datetime | None = None,
                      market_timezone: str = "Asia/Taipei",
                      session_close: str = "13:30") -> tuple[dict[str, Mapping[str, Any]], str]:
    required = set(session_dates)
    by_date: dict[str, Mapping[str, Any]] = {}
    for bar in bars:
        day = str(bar.get("date") or "")
        if day not in required:
            continue
        if day in by_date:
            return {}, "stock_window_invalid"
        by_date[day] = bar
    selected: list[Mapping[str, Any]] = []
    for session_date in session_dates:
        bar = by_date.get(session_date)
        if bar is None:
            return {}, "required_session_missing"
        reason = bar_validation_reason(
            bar, session_date=session_date, today=today, now=now,
            market_timezone=market_timezone, session_close=session_close,
        )
        if reason:
            return {}, reason
        selected.append(bar)
    identities = {
        (bar.get("provider"), bar.get("source_contract"), bar.get("adjustment_mode"),
         bar.get("volume_unit"))
        for bar in selected
    }
    capture_ids = {str(bar.get("capture_id") or "") for bar in selected}
    if len(identities) > 1 or len(capture_ids) != len(selected):
        return {}, "stock_window_invalid"
    return by_date, ""


def _not_ready(reason: str, sessions: Sequence[str], entry_status: str = "PENDING") -> dict[str, Any]:
    if reason == "daily_bar_not_final":
        return {"evaluation_status": "PENDING", "entry_status": entry_status,
                "reason_code": "", "expected_session_dates": list(sessions)}
    return {"evaluation_status": "INSUFFICIENT_DATA", "entry_status": "INSUFFICIENT_DATA",
            "reason_code": reason, "expected_session_dates": list(sessions)}


def evaluate_forward_stock(*, bars: Sequence[Mapping[str, Any]],
                           expected_session_dates: Sequence[str], horizon_sessions: int,
                           today: date, now: datetime | None = None,
                           market_timezone: str = "Asia/Taipei",
                           session_close: str = "13:30") -> dict[str, Any]:
    """Evaluate next-session-close entry then the h-th following session close."""
    h = max(1, int(horizon_sessions))
    count = h + 1
    sessions = list(expected_session_dates[:count])
    if len(sessions) != count or not all(_valid_session(x) for x in sessions):
        return _not_ready("calendar_unverifiable", sessions)
    if sessions != sorted(set(sessions)):
        return _not_ready("calendar_unverifiable", sessions)
    clock = now or datetime.now(timezone.utc)
    if any(_session_due(day, now=clock, market_timezone=market_timezone,
                        session_close=session_close) is not True for day in sessions):
        return {"evaluation_status": "PENDING", "entry_status": "PENDING",
                "reason_code": "", "expected_session_dates": sessions}
    by_date, reason = _validated_window(
        bars, sessions, today=today, now=clock, market_timezone=market_timezone,
        session_close=session_close,
    )
    if reason:
        return _not_ready(reason, sessions)
    entry_day, exit_day = sessions[0], sessions[-1]
    entry_price = _number(by_date[entry_day].get("close"))
    exit_price = _number(by_date[exit_day].get("close"))
    if not entry_price or not exit_price:
        return _not_ready("stock_window_invalid", sessions)
    return {
        "evaluation_status": "COMPLETE", "entry_status": "FILLED", "barrier_order": "NONE",
        "reason_code": "", "expected_session_dates": sessions,
        "entry_session_date": entry_day, "exit_session_date": exit_day,
        "entry_price": entry_price, "exit_price": exit_price,
        "stock_gross_return_pct": (exit_price / entry_price - 1.0) * 100.0,
        "entry_time_precision": "SESSION_ONLY",
        "stock_bars": [dict(by_date[day]) for day in sessions],
    }


def evaluate_execution_stock(*, bars: Sequence[Mapping[str, Any]],
                             expected_session_dates: Sequence[str], horizon_sessions: int,
                             today: date, action: str, is_holding_snapshot: bool,
                             entry_low: Any, entry_high: Any, stop_loss: Any,
                             target_price: Any, now: datetime | None = None,
                             market_timezone: str = "Asia/Taipei",
                             session_close: str = "13:30") -> dict[str, Any]:
    """Apply a three-session long limit entry and overnight gap-first exit policy."""
    h = max(1, int(horizon_sessions))
    normalized_action = str(action or "").lower()
    if not ((normalized_action == "buy" and not is_holding_snapshot)
            or (normalized_action == "add" and is_holding_snapshot)):
        return {"evaluation_status": "COMPLETE", "entry_status": "NOT_FILLED",
                "barrier_order": "NONE", "reason_code": "order_not_applicable",
                "expected_session_dates": []}
    low, limit, stop, target = map(_number, (entry_low, entry_high, stop_loss, target_price))
    if (low is None or limit is None or stop is None or target is None
            or min(low, limit, stop, target) <= 0 or low > limit or not stop < limit < target):
        return {"evaluation_status": "INSUFFICIENT_DATA", "entry_status": "INSUFFICIENT_DATA",
                "barrier_order": "NONE", "reason_code": "entry_window_invalid",
                "expected_session_dates": list(expected_session_dates[:3 + h])}
    required_count = 3 + h
    sessions = list(expected_session_dates[:required_count])
    entries = sessions[:3]
    if len(entries) < 3 or not all(_valid_session(x) for x in sessions):
        return _not_ready("calendar_unverifiable", sessions)
    if sessions != sorted(set(sessions)):
        return _not_ready("calendar_unverifiable", sessions)
    by_date: dict[str, Mapping[str, Any]] = {}
    needed = set(sessions)
    for bar in bars:
        day = str(bar.get("date") or "")
        if day not in needed:
            continue
        if day in by_date:
            return _not_ready("stock_window_invalid", sessions)
        by_date[day] = bar

    clock = now or datetime.now(timezone.utc)
    used_bars: list[Mapping[str, Any]] = []
    source_identity: tuple[Any, ...] | None = None

    def use_bar(day: str, entry_status: str) -> dict[str, Any] | None:
        nonlocal source_identity
        if _session_due(day, now=clock, market_timezone=market_timezone,
                        session_close=session_close) is not True:
            return {"evaluation_status": "PENDING", "entry_status": entry_status,
                    "reason_code": "", "expected_session_dates": sessions}
        bar = by_date.get(day)
        if bar is None:
            return {"evaluation_status": "INSUFFICIENT_DATA", "entry_status": entry_status,
                    "reason_code": "required_session_missing", "expected_session_dates": sessions}
        reason = bar_validation_reason(
            bar, session_date=day, today=today, now=clock,
            market_timezone=market_timezone, session_close=session_close,
        )
        if reason:
            return _not_ready(reason, sessions, entry_status)
        identity = (bar.get("provider"), bar.get("source_contract"),
                    bar.get("adjustment_mode"), bar.get("volume_unit"))
        if any(str(item.get("capture_id")) == str(bar.get("capture_id")) for item in used_bars):
            return _not_ready("stock_window_invalid", sessions, entry_status)
        if source_identity is None:
            source_identity = identity
        elif source_identity != identity:
            return _not_ready("stock_window_invalid", sessions, entry_status)
        used_bars.append(bar)
        return None

    entry_index: int | None = None
    fill_price: float | None = None
    for index, day in enumerate(entries):
        not_ready = use_bar(day, "PENDING")
        if not_ready:
            return not_ready
        bar = used_bars[-1]
        opened, day_high, day_low = (_number(bar[key]) for key in ("open", "high", "low"))
        if opened <= stop:
            return {"evaluation_status": "COMPLETE", "entry_status": "NOT_FILLED",
                    "barrier_order": "NONE", "reason_code": "entry_invalidated_at_open",
                    "expected_session_dates": sessions,
                    "stock_bars": [dict(x) for x in used_bars]}
        if opened <= limit:
            entry_index, fill_price = index, opened
            break
        if day_low <= limit:
            entry_index, fill_price = index, limit
            break
    if entry_index is None:
        return {"evaluation_status": "COMPLETE", "entry_status": "NOT_FILLED",
                "barrier_order": "NONE", "reason_code": "entry_window_expired",
                "expected_session_dates": sessions,
                "stock_bars": [dict(x) for x in used_bars]}

    exit_dates = sessions[entry_index + 1:entry_index + 1 + h]
    if len(exit_dates) < h:
        return _not_ready("calendar_unverifiable", sessions, "FILLED")
    for day in exit_dates:
        not_ready = use_bar(day, "FILLED")
        if not_ready:
            return {
                **not_ready,
                "entry_status": "FILLED",
                "entry_session_date": entries[entry_index],
                "entry_price": fill_price,
                "entry_time_precision": "SESSION_ONLY",
                "stock_bars": [dict(x) for x in used_bars],
            }
        bar = used_bars[-1]
        opened, high, low_of_day, close = (_number(bar[name]) for name in ("open", "high", "low", "close"))
        exit_price = None
        order = "NONE"
        if opened <= stop:
            exit_price, order = opened, "STOP_FIRST"
        elif opened >= target:
            exit_price, order = opened, "TARGET_FIRST"
        elif low_of_day <= stop and high >= target:
            return {"evaluation_status": "AMBIGUOUS", "entry_status": "FILLED",
                    "barrier_order": "BOTH_SAME_BAR", "reason_code": "barrier_order_ambiguous",
                    "entry_session_date": entries[entry_index], "entry_price": fill_price,
                    "exit_session_date": day, "exit_price": None,
                    "stock_gross_return_pct": None, "entry_time_precision": "SESSION_ONLY",
                    "expected_session_dates": sessions,
                    "stock_bars": [dict(x) for x in used_bars]}
        elif low_of_day <= stop:
            exit_price, order = stop, "STOP_FIRST"
        elif high >= target:
            exit_price, order = target, "TARGET_FIRST"
        elif day != exit_dates[-1]:
            continue
        else:
            exit_price = close
        gross = (exit_price / fill_price - 1.0) * 100.0
        return {"evaluation_status": "COMPLETE", "entry_status": "FILLED",
                "barrier_order": order, "reason_code": "",
                "entry_session_date": entries[entry_index], "entry_price": fill_price,
                "exit_session_date": day, "exit_price": exit_price,
                "stock_gross_return_pct": gross, "entry_time_precision": "SESSION_ONLY",
                "expected_session_dates": sessions,
                "stock_bars": [dict(x) for x in used_bars]}
    raise AssertionError("execution exit sequence did not terminate")


def retry_delay(attempt_number: int):
    """The 1h/6h/24h backoff, capped at 24 hours for later attempts."""
    from datetime import timedelta
    idx = min(max(0, int(attempt_number) - 1), len(RETRY_BACKOFF_HOURS) - 1)
    return timedelta(hours=RETRY_BACKOFF_HOURS[idx])
