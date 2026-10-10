from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, HTTPException, Query
from datetime import date, datetime, time as clock_time, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.models import MarketCode

router = APIRouter()

_TAIPEI = ZoneInfo("Asia/Taipei")
_REGULAR_SESSION_END = clock_time(13, 30)
_AUTO_LOOKBACK_DAYS = 30
_AUTO_BAR_REQUEST_BUDGET = 3
_AUTO_TOTAL_DEADLINE_SEC = 8.0


def _taipei_now() -> datetime:
    return datetime.now(_TAIPEI)


def _parse_iso_date(value: str, name: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{name} 必須為 YYYY-MM-DD") from exc


def _parse_source_date(value: object, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"twmd {name} must use YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise ValueError(f"twmd {name} must use YYYY-MM-DD") from exc


def _selected_intraday_date(payload: dict, expected_instrument: str, timeframe: str,
                            selected_date: date, *, require_envelope: bool = False) -> None:
    """Reject cross-request responses before the chart can mix their rows."""
    instrument_id = payload.get("instrument_id")
    payload_timeframe = payload.get("timeframe")
    if require_envelope and (instrument_id is None or payload_timeframe is None):
        raise ValueError("intraday response is missing its instrument or timeframe identity")
    if instrument_id is not None and instrument_id != expected_instrument:
        raise ValueError("intraday response instrument does not match request")
    if payload_timeframe is not None and payload_timeframe != timeframe:
        raise ValueError("intraday response timeframe does not match request")
    for field in ("start_date", "end_date"):
        value = payload.get(field)
        if value is not None and _parse_source_date(value, field) != selected_date:
            raise ValueError(f"intraday response {field} does not match requested date")

    raw_bars = payload.get("bars", [])
    if not isinstance(raw_bars, list):
        raise ValueError("intraday response bars must be a list")
    raw_coverage = payload.get("coverage", [])
    if not isinstance(raw_coverage, list):
        raise ValueError("intraday response coverage must be a list")
    if any(not isinstance(row, dict) or not isinstance(row.get("timestamp"), str) for row in raw_bars):
        raise ValueError("intraday bar rows require a timestamp")
    for row in [*raw_bars, *raw_coverage]:
        if not isinstance(row, dict):
            raise ValueError("intraday response rows must be objects")
        row_instrument = row.get("instrument_id")
        row_timeframe = row.get("timeframe")
        if row_instrument is not None and row_instrument != expected_instrument:
            raise ValueError("intraday row instrument does not match request")
        if row_timeframe is not None and row_timeframe != timeframe:
            raise ValueError("intraday row timeframe does not match request")
        trade_date = row.get("trade_date")
        if trade_date is not None and _parse_source_date(trade_date, "trade_date") != selected_date:
            raise ValueError("intraday row trade date does not match request")
        for field in ("timestamp", "interval_start", "interval_end", "provider_timestamp"):
            timestamp = row.get(field)
            if timestamp is None:
                continue
            try:
                parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"intraday row {field} is invalid") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError(f"intraday row {field} must include a timezone")
            if parsed.astimezone(_TAIPEI).date() != selected_date:
                raise ValueError(f"intraday row {field} date does not match request")


def _automatic_window(now: datetime) -> tuple[date, date]:
    taipei = now.astimezone(_TAIPEI)
    end = taipei.date()
    if taipei.timetz().replace(tzinfo=None) < _REGULAR_SESSION_END:
        end -= timedelta(days=1)
    return end - timedelta(days=_AUTO_LOOKBACK_DAYS - 1), end


def _coverage_candidates(read, start: date, end: date, now: datetime) -> list[date]:
    from src.platform.scheduling.trading_calendar import calendar_status

    taipei = now.astimezone(_TAIPEI)
    candidates = []
    for row in read.coverage:
        trade_date = row.trade_date
        local = calendar_status("TW", trade_date)
        local_known = local["status"] == "known"
        local_open = local_known and local["is_trading_day"] is True
        local_closed = local_known and local["is_trading_day"] is False
        if (trade_date < start or trade_date > end or trade_date > taipei.date()
                or (trade_date == taipei.date() and taipei.timetz().replace(tzinfo=None) < _REGULAR_SESSION_END)
                or local_closed or row.calendar_status == "official_closed"):
            continue
        calendar_confirms_open = local_open or row.calendar_status == "observed_open"
        retained_session = (
            row.status in {"available", "incomplete"}
            and row.expected_minutes == 270
            and row.observed_minutes > 0
            and row.pending_minutes == 0
            and (row.status != "available" or (row.observed_minutes == 270 and row.missing_minutes == 0))
        )
        if calendar_confirms_open and retained_session:
            candidates.append(trade_date)
    return sorted(set(candidates), reverse=True)


def _has_drawable_priced_bars(payload: dict) -> bool:
    rows = payload.get("bars")
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "observed" or row.get("finalized") is not True:
            continue
        close = row.get("close")
        if close is None or isinstance(close, bool):
            continue
        try:
            value = Decimal(str(close))
        except (InvalidOperation, ValueError):
            continue
        if value.is_finite() and value > 0:
            return True
    return False


def _complete_single_date_payload(payload: dict, timeframe: str) -> bool:
    expected = 270 if timeframe == "1m" else 54
    start_date, end_date = payload.get("start_date"), payload.get("end_date")
    return (
        start_date is not None and start_date == end_date
        and payload.get("coverage_complete") is True
        and payload.get("truncated") is False
        and payload.get("total_count") == expected
        and payload.get("returned_count") == expected
        and isinstance(payload.get("bars"), list)
        and len(payload["bars"]) == expected
    )


def _empty_intraday_selection(symbol: str, timeframe: str, limit: int,
                              date_selection: dict, *, raw: dict | None = None) -> dict:
    return {
        **(raw or {}),
        "symbol": symbol,
        "market": "TW",
        "timeframe": timeframe,
        "price_kind": "intraday",
        "adjustment_mode": "provider_reported",
        "provider": "shioaji",
        "timezone": "Asia/Taipei",
        "timestamp_meaning": "interval_start",
        "units": {"currency": "TWD", "price": "TWD", "volume": "shares", "turnover": "TWD"},
        "limit": limit,
        "returned_count": 0,
        "total_count": 0,
        "truncated": False,
        "partial": True,
        "coverage_complete": False,
        "availability": "unavailable",
        "bars": [],
        "klines": [],
        "summary": None,
        "live_collection": False,
        "usable_for_trading": False,
        "selection_coverage": raw,
        "date_selection": date_selection,
    }


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US/TW")
    days: int | None = Field(default=60, description="K線天數")
    interval: str | None = Field(default="1d", description="週期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US/TW")


class KlineSummaryBatchRequest(BaseModel):
    items: list[KlineSummaryItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支援的市場: {market}")


def _serialize_klines(klines) -> list[dict]:
    return [
        {
            "date": k.date,
            "open": k.open,
            "close": k.close,
            "high": k.high,
            "low": k.low,
            "volume": k.volume,
            "provider": getattr(k, "provider", None),
            "adjustment_mode": getattr(k, "adjustment_mode", None),
            "volume_unit": getattr(k, "volume_unit", None),
        }
        for k in klines
    ]


def _aggregate_klines(klines, interval: str) -> list:
    """Aggregate daily klines to week/month."""

    iv = (interval or "1d").lower()
    if iv in ("1d", "day", "d"):
        return klines
    if iv not in ("1w", "1m", "week", "month", "w", "m"):
        return klines

    parsed = []
    for k in klines or []:
        try:
            dt = datetime.strptime(k.date, "%Y-%m-%d")
        except Exception:
            continue
        parsed.append((dt, k))

    parsed.sort(key=lambda x: x[0])
    buckets: dict[str, list] = {}
    for dt, k in parsed:
        if iv in ("1w", "week", "w"):
            y, w, _ = dt.isocalendar()
            key = f"{y:04d}-W{w:02d}"
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
        buckets.setdefault(key, []).append((dt, k))

    out = []
    for _, items in buckets.items():
        items.sort(key=lambda x: x[0])
        first = items[0][1]
        last = items[-1][1]
        high = max(it[1].high for it in items)
        low = min(it[1].low for it in items)
        vol = sum(it[1].volume for it in items)
        out.append(
            type(first)(
                date=items[-1][0].strftime("%Y-%m-%d"),
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=vol,
                provider=getattr(first, "provider", None),
                adjustment_mode=getattr(first, "adjustment_mode", None),
                volume_unit=getattr(first, "volume_unit", None),
            )
        )
    out.sort(key=lambda k: k.date)
    return out


@router.get("/{symbol}/intraday")
def get_intraday_klines(symbol: str, market: str = "TW", timeframe: str = "1m",
                       limit: int | None = Query(None, ge=1, le=1000),
                       start_date: str | None = None, end_date: str | None = None,
                       date_mode: str | None = None, trade_date: str | None = None):
    """Stored minute bars. Missing/no-trade slots retain null prices and their status."""
    from marketdata.symbol import Symbol
    from marketdata.vendors.twmd import TwmdClient, number
    from marketdata.errors import TwmdReadError
    from src.platform.marketdata.marketdata_client import twmd_config
    from src.platform.scheduling.trading_calendar import calendar_status, previous_trading_day

    if market != "TW" or timeframe not in {"1m", "5m"}:
        raise HTTPException(400, "台股歷史分K支援 1m/5m")
    if date_mode is not None and date_mode not in {"auto", "selected", "previous"}:
        raise HTTPException(400, "date_mode 僅支援 auto/selected/previous")
    if bool(start_date) != bool(end_date):
        raise HTTPException(400, "start_date/end_date 必須一起提供")
    if start_date and (trade_date or date_mode == "previous"):
        raise HTTPException(400, "日期範圍不可和 trade_date/date_mode=previous 一起提供")

    expected_limit = 270 if timeframe == "1m" else 54
    requested_limit = limit if limit is not None else (
        expected_limit if date_mode == "auto" else 270
    )
    if date_mode == "auto" and not start_date and requested_limit > expected_limit:
        raise HTTPException(400, f"{timeframe} 單日 limit 不可超過 {expected_limit}")

    explicit_range: tuple[date, date] | None = None
    if start_date:
        start = _parse_iso_date(start_date, "start_date")
        end = _parse_iso_date(end_date or "", "end_date")
        if (end - start).days < 0 or (end - start).days >= 30:
            raise HTTPException(400, "分K日期範圍最多 30 天")
        explicit_range = (start, end)

    selected_date: date | None = None
    selection_mode = date_mode or "legacy"
    requested_date: date | None = None
    if explicit_range:
        selected_date = explicit_range[0] if explicit_range[0] == explicit_range[1] else None
        selection_mode = "selected"
        requested_date = selected_date
    elif date_mode in {"selected", "previous"}:
        if not trade_date:
            raise HTTPException(400, "selected/previous 模式需要 trade_date")
        requested_date = _parse_iso_date(trade_date, "trade_date")
        if date_mode == "selected":
            selected_date = requested_date
        else:
            selected_date = previous_trading_day("TW", requested_date)
            if selected_date is None:
                local = calendar_status("TW", requested_date)
                return _empty_intraday_selection(
                    symbol, timeframe, requested_limit,
                    {"mode": "previous", "requested_date": requested_date.isoformat(),
                     "selected_date": None, "reason": "calendar_unknown",
                     "calendar_status": local["status"], "calendar": local},
                )
    elif trade_date:
        # A date always means an explicit selection, even when older callers omit date_mode.
        selected_date = requested_date = _parse_iso_date(trade_date, "trade_date")
        selection_mode = "selected"

    client = TwmdClient(twmd_config())
    symbol_obj = Symbol.parse(symbol, "TW")
    if date_mode == "auto" and not explicit_range and selected_date is None:
        if symbol_obj.venue not in {"TWSE", "TPEX"}:
            raise HTTPException(400, "自動選日需要 TWSE:/TPEX: 標的識別")
        instrument_id = symbol_obj.identity
        now = _taipei_now()
        window_start, window_end = _automatic_window(now)
        deadline = monotonic() + _AUTO_TOTAL_DEADLINE_SEC
        try:
            coverage_read = client.bars_coverage(
                symbol_obj, window_start, window_end,
                timeout_sec=max(0.001, min(3.0, deadline - monotonic())),
            )
        except (ValueError, TwmdReadError) as exc:
            raise HTTPException(503, f"twmd 分K覆蓋查詢不可用: {exc}") from exc
        if not coverage_read.schema_ready:
            return _empty_intraday_selection(
                symbol, timeframe, requested_limit,
                {"mode": "auto", "requested_date": None, "selected_date": None,
                 "reason": "schema_not_ready", "calendar_status": "unknown",
                 "coverage_window": {"start_date": window_start.isoformat(),
                                     "end_date": window_end.isoformat()}},
                raw=coverage_read.raw,
            )

        candidates = _coverage_candidates(coverage_read, window_start, window_end, now)
        requests = 0
        for candidate in candidates[:_AUTO_BAR_REQUEST_BUDGET]:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            requests += 1
            try:
                payload, _headers = client.get_response(
                    "bars", timeout_sec=remaining, retries=0,
                    instrument_id=instrument_id, timeframe=timeframe,
                    limit=requested_limit, start_date=candidate.isoformat(),
                    end_date=candidate.isoformat(), session="regular",
                )
            except TwmdReadError as exc:
                raise HTTPException(503, f"twmd 分K查詢不可用: {exc}") from exc
            if not isinstance(payload, dict):
                raise HTTPException(503, "twmd 分K回應格式無效")
            try:
                _selected_intraday_date(payload, instrument_id, timeframe, candidate, require_envelope=True)
            except ValueError as exc:
                raise HTTPException(502, f"twmd 分K回應識別不一致: {exc}") from exc
            if not _has_drawable_priced_bars(payload):
                continue
            coverage_row = next((row for row in coverage_read.coverage if row.trade_date == candidate), None)
            local = calendar_status("TW", candidate)
            try:
                result = _finish_intraday_payload(payload, symbol, timeframe, requested_limit, number)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise HTTPException(502, f"twmd 分K回應格式無效: {exc}") from exc
            result["selection_coverage"] = coverage_read.raw
            result["date_selection"] = {
                "mode": "auto", "requested_date": None, "selected_date": candidate.isoformat(),
                "reason": "latest_known_completed_priced_session",
                "calendar_status": "known_open" if local["status"] == "known" and local["is_trading_day"]
                    else coverage_row.calendar_status if coverage_row else "unknown",
                "calendar": local,
                "as_of_date": now.astimezone(_TAIPEI).date().isoformat(),
                "as_of_calendar": calendar_status("TW", now.astimezone(_TAIPEI).date()),
                "coverage_status": coverage_row.status if coverage_row else None,
                "display_coverage_complete": _complete_single_date_payload(payload, timeframe),
                "coverage_calendar_status": coverage_row.calendar_status if coverage_row else "unknown",
                "coverage_window": {"start_date": window_start.isoformat(),
                                    "end_date": window_end.isoformat()},
                "bar_request_count": requests,
                "bar_request_budget": _AUTO_BAR_REQUEST_BUDGET,
            }
            return result
        reason = "no_known_completed_trading_date" if not candidates else (
            "request_budget_exhausted" if monotonic() >= deadline or len(candidates) > requests
            else "no_priced_complete_candidate"
        )
        return _empty_intraday_selection(
            symbol, timeframe, requested_limit,
            {"mode": "auto", "requested_date": None, "selected_date": None,
             "reason": reason, "calendar_status": "unknown",
             "coverage_window": {"start_date": window_start.isoformat(),
                                 "end_date": window_end.isoformat()},
             "bar_request_count": requests, "bar_request_budget": _AUTO_BAR_REQUEST_BUDGET},
            raw=coverage_read.raw,
        )

    params = {"limit": requested_limit}
    if explicit_range:
        params.update(start_date=explicit_range[0].isoformat(), end_date=explicit_range[1].isoformat())
    elif selected_date:
        params.update(start_date=selected_date.isoformat(), end_date=selected_date.isoformat())
    try:
        payload = client.bars(symbol_obj, timeframe=timeframe, **params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not payload:
        raise HTTPException(503, "twmd 分K資料來源不可用")
    if not isinstance(payload, dict):
        raise HTTPException(502, "twmd 分K回應格式無效")
    if selected_date is not None:
        expected_instrument = symbol_obj.identity if symbol_obj.venue else payload.get("instrument_id")
        try:
            _selected_intraday_date(payload, expected_instrument, timeframe, selected_date)
        except ValueError as exc:
            raise HTTPException(502, f"twmd 分K回應識別不一致: {exc}") from exc
    try:
        result = _finish_intraday_payload(payload, symbol, timeframe, requested_limit, number)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(502, f"twmd 分K回應格式無效: {exc}") from exc
    if selected_date is not None:
        local = calendar_status("TW", selected_date)
        coverage_row = next((row for row in payload.get("coverage", [])
                             if row.get("trade_date") == selected_date.isoformat()), None)
        result["date_selection"] = {
            "mode": selection_mode, "requested_date": requested_date.isoformat() if requested_date else None,
            "selected_date": selected_date.isoformat(),
            "reason": "previous_trading_day" if selection_mode == "previous" else "manual_selection",
            "calendar_status": local["status"], "calendar": local,
            "coverage_status": coverage_row.get("status") if coverage_row else None,
            "coverage_calendar_status": coverage_row.get("calendar_status", "unknown") if coverage_row else "unknown",
        }
    elif explicit_range:
        result["date_selection"] = {"mode": "selected", "requested_date": None,
                                    "selected_date": None, "reason": "explicit_range",
                                    "calendar_status": "unknown"}
    return result


def _finish_intraday_payload(payload: dict, symbol: str, timeframe: str, limit: int, number) -> dict:
    raw_bars = payload.get("bars", [])
    if not isinstance(raw_bars, list) or any(
        not isinstance(bar, dict) or not isinstance(bar.get("timestamp"), str) for bar in raw_bars
    ):
        raise ValueError("intraday bar rows require a timestamp")
    bars = [{**bar, "date": bar["timestamp"],
             **{field: number(bar.get(field)) for field in ("open", "close", "high", "low", "volume", "turnover")}}
            for bar in raw_bars]
    valid = [bar for bar in bars if bar.get("finalized") and bar.get("status") == "observed"
             and all(bar.get(field) is not None for field in ("open", "close", "high", "low", "volume"))]
    summary = None
    if payload.get("coverage_complete") and payload.get("truncated") is False and len(valid) >= 20:
        from src.platform.marketdata.collectors.kline_collector import KlineData
        data = [KlineData(date=bar["date"], open=bar["open"], close=bar["close"],
                          high=bar["high"], low=bar["low"], volume=bar["volume"]) for bar in valid]
        from dataclasses import asdict
        summary = asdict(KlineCollector(MarketCode.TW).get_technical_indicators(klines=data))
    return {**payload, "symbol": symbol, "market": "TW", "timeframe": timeframe,
            "limit": payload.get("limit", limit), "klines": bars, "summary": summary,
            "display_coverage_complete": _complete_single_date_payload(payload, timeframe),
            "live_collection": False, "usable_for_trading": False}


@router.get("/{symbol}")
def get_klines(symbol: str, market: str = "CN", days: int = 60, interval: str = "1d"):
    """獲取單隻股票K線資料"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    klines = collector.get_klines(symbol, days=days)
    klines = _aggregate_klines(klines, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(klines),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest):
    """批次獲取K線資料"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        collector = KlineCollector(market_code)
        days = item.days or 60
        interval = item.interval or "1d"
        klines = collector.get_klines(item.symbol, days=days)
        klines = _aggregate_klines(klines, interval)
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(klines),
            }
        )

    return results


@router.get("/{symbol}/summary")
def get_kline_summary(symbol: str, market: str = "CN"):
    """獲取單隻股票K線摘要"""
    market_code = _parse_market(market)
    collector = KlineCollector(market_code)
    summary = collector.get_kline_summary(symbol)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "summary": summary,
    }


@router.post("/summary/batch")
def get_kline_summary_batch(payload: KlineSummaryBatchRequest):
    """批次獲取K線摘要"""
    if not payload.items:
        return []

    market_codes = [_parse_market(item.market) for item in payload.items]

    def load_one(index: int):
        item = payload.items[index]
        market_code = market_codes[index]
        try:
            summary = KlineCollector(market_code).get_kline_summary(item.symbol)
        except Exception as exc:
            summary = {"error": str(exc)}
        return {
            "symbol": item.symbol,
            "market": market_code.value,
            "summary": summary,
        }

    # 與前端原先的併發上限保持一致，減少批次介面對資料來源的瞬時壓力。
    with ThreadPoolExecutor(max_workers=min(5, len(payload.items))) as executor:
        futures = [executor.submit(load_one, index) for index in range(len(payload.items))]
        return [future.result() for future in futures]
