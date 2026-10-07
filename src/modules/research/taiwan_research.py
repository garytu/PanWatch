"""Bounded, source-preserving Taiwan issuer research aggregation."""

from __future__ import annotations

import calendar
import copy
import hashlib
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from marketdata.errors import TwmdReadError
from marketdata.symbol import Symbol
from marketdata.vendors.twmd import TwmdClient

from src.modules.research.twmd_profile_revenue import (
    ResearchDataBlock,
    company_profile_block,
    monthly_revenue_block,
)
from src.platform.marketdata.marketdata_client import twmd_config

_TAIPEI = ZoneInfo("Asia/Taipei")
_DEFAULT_DAYS = 30
_DEFAULT_MONTHS = 12
_MAX_DAYS = 366
_MAX_MONTHS = 120
_REQUEST_DEADLINE_SECONDS = 25
_CATALOG_SECURITY_TYPES = {
    "EQUITY", "ETF", "ETN", "WARRANT", "PREFERRED", "TDR", "REIT", "OTHER",
}
_CACHE_LIMIT = 256
_CACHE_TTLS = {
    "catalog": 300,
    "valuation": 300,
    "institutional_flows": 300,
    "company_profile": 21_600,
    "monthly_revenues": 1_800,
}
_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_CACHE_LOCK = threading.RLock()
_REQUEST_SLOTS = threading.BoundedSemaphore(4)
_READ_SLOTS = threading.BoundedSemaphore(4)
_READ_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tw-research")
_FRESHNESS_HINTS = {
    "valuation": (
        "daily",
        "日資料；距今按日曆日計算。尚未核對交易日曆或來源更新期限；未取得覆蓋不代表休市或零值。",
    ),
    "institutional_flows": (
        "daily",
        "日資料；距今按日曆日計算。尚未核對交易日曆或來源更新期限；未取得覆蓋不代表休市或零值。",
    ),
    "company_profile": (
        "latest_only_snapshot",
        "公司資料只提供最新快照；保留公司列與整體快照的接收時間分開。來源未提供更新期限，不能查詢歷史快照。",
    ),
    "monthly_revenues": (
        "monthly",
        "月資料；期別距今從該月月底計算。來源未提供發布時間或申報期限；未取得覆蓋不代表零營收或未申報。",
    ),
}


def _scope(config: dict) -> tuple[str, str]:
    base_url = str(config.get("base_url") or "").rstrip("/")
    credential_hash = hashlib.sha256(str(config.get("token") or "").encode()).hexdigest()
    return base_url, credential_hash


def _public_provider_scope(config: dict) -> str:
    base_url = str(config.get("base_url") or "")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname
        if not hostname:
            return "configured_twmd_service"
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme or 'http'}://{host}{port}"
    except ValueError:
        return "configured_twmd_service"


class _ReadLease:
    """Own one global read permit until the underlying worker actually stops."""

    def __init__(self):
        self._lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        _READ_SLOTS.release()


def _submit_read(function, *args):
    if not _READ_SLOTS.acquire(blocking=False):
        raise RuntimeError("concurrency_limit")
    lease = _ReadLease()

    def run():
        try:
            return function(*args)
        finally:
            lease.release()

    try:
        future = _READ_POOL.submit(run)
    except Exception:
        lease.release()
        raise
    future.add_done_callback(lambda done: lease.release() if done.cancelled() else None)
    return future


def taiwan_research_identity(symbol: str, quote_identity: str | None = None) -> str:
    """Use a quote only to disambiguate the same bare Taiwan code."""
    parsed = Symbol.parse(symbol, "TW")
    if parsed.market.value != "TW":
        raise ValueError("Taiwan research requires a Taiwan instrument")
    if parsed.venue:
        return parsed.identity
    if quote_identity:
        try:
            quoted = Symbol.parse(quote_identity, "TW")
            if quoted.venue in {"TWSE", "TPEX"} and quoted.code == parsed.code:
                return quoted.identity
        except (TypeError, ValueError):
            pass
    return symbol


class _BoundedTwmdClient(TwmdClient):
    """Clamp every transport attempt to the service budget and disable retries."""

    def get_response(self, path: str, *, timeout_sec: float | None = None, retries: int | None = None, **params):
        configured = timeout_sec
        if configured is None:
            configured = self.config.get("timeout_sec") or 5
        try:
            bounded_timeout = min(float(configured), 20.0)
        except (TypeError, ValueError):
            bounded_timeout = 5.0
        return super().get_response(
            path,
            timeout_sec=bounded_timeout,
            retries=0,
            **params,
        )


def _cache_get(key: tuple) -> Any | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= now:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(value)


def _cache_set(key: tuple, value: Any, ttl: int) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def clear_taiwan_research_cache() -> None:
    """Clear bounded service caches; used by deterministic offline tests."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _month(value: str, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError(f"{label} must use YYYY-MM")
    parsed = date.fromisoformat(f"{value}-01")
    if parsed.strftime("%Y-%m") != value:
        raise ValueError(f"{label} must use YYYY-MM")
    return parsed


def _date(value: str, label: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} must use YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD")
    return parsed


def _previous_month(today: date) -> date:
    return date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)


def _month_offset(value: date, offset: int) -> date:
    ordinal = value.year * 12 + value.month - 1 + offset
    return date(ordinal // 12, ordinal % 12 + 1, 1)


def _bounds(
    *,
    today: date,
    start_date: str | None,
    end_date: str | None,
    start_month: str | None,
    end_month: str | None,
) -> tuple[date, date, date, date]:
    if (start_date is None) != (end_date is None):
        raise ValueError("start_date and end_date must be provided together")
    end = today - timedelta(days=1) if end_date is None else _date(end_date, "end_date")
    start = end - timedelta(days=_DEFAULT_DAYS - 1) if start_date is None else _date(start_date, "start_date")
    if start > end:
        raise ValueError("start_date must not be after end_date")
    if start < date(2024, 1, 1):
        raise ValueError("research date range begins on 2024-01-01")
    if end >= today:
        raise ValueError("end_date must be before the current Asia/Taipei date")
    if (end - start).days + 1 > _MAX_DAYS:
        raise ValueError("date range is limited to 366 calendar days")

    if (start_month is None) != (end_month is None):
        raise ValueError("start_month and end_month must be provided together")
    month_end = _previous_month(today) if end_month is None else _month(end_month, "end_month")
    month_start = _month_offset(month_end, -(_DEFAULT_MONTHS - 1)) if start_month is None else _month(start_month, "start_month")
    if month_start > month_end:
        raise ValueError("start_month must not be after end_month")
    current_month = today.replace(day=1)
    if month_start < date(2024, 1, 1):
        raise ValueError("monthly revenue range begins on 2024-01")
    if month_end > current_month:
        raise ValueError("monthly revenue range cannot include a future Taipei month")
    month_count = (month_end.year - month_start.year) * 12 + month_end.month - month_start.month + 1
    if month_count > _MAX_MONTHS:
        raise ValueError("monthly revenue range is limited to 120 months")
    return start, end, month_start, month_end


def _empty_evidence(instrument_id: str | None, endpoint: str, selectors: dict[str, str], config: dict) -> dict:
    return {
        "provider": "twmd",
        "instrument_id": instrument_id,
        "endpoint": endpoint,
        "selectors": selectors,
        "source_contract": None,
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
        "provider_scope": _public_provider_scope(config),
    }


def _error_block(
    reason: str,
    endpoint: str,
    selectors: dict[str, str],
    config: dict,
    *,
    status: str = "error",
    instrument_id: str | None = None,
    http_status: int | None = None,
) -> ResearchDataBlock:
    evidence = _empty_evidence(instrument_id, endpoint, selectors, config)
    evidence["http_status"] = http_status
    return ResearchDataBlock(data=None, status=status, reason=reason, evidence=evidence)


def _row_uniform(rows: list[dict], key: str) -> Any:
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return values[0] if all(value == values[0] for value in values) else "mixed"


def _valuation_block(read) -> ResearchDataBlock:
    rows = [asdict(row) for row in read.data]
    return ResearchDataBlock(
        data={"instrument_id": read.instrument_id, "observations": rows},
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {"instrument_id": read.instrument_id, "start": read.start_date, "end": read.end_date},
            "source_contract": _row_uniform(rows, "source_contract"),
            "period": {"trade_dates": [row["trade_date"] for row in rows]},
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": _row_uniform(rows, "received_at_utc"),
            "per_period_provenance": rows,
            "served_at": None,
            "units": {
                "close_price": "TWD per share",
                "pe_ratio": "provider-reported multiple; period semantics unspecified",
                "pb_ratio": "provider-reported multiple",
                "dividend_yield_pct": "percent",
                "dividend_per_share": "source currency; see each observation",
            },
            "dataset_coverage": read.coverage_header,
            "selected_instrument_presence": read.selected_instrument_presence,
            "response_headers": {
                key.lower(): value for key, value in read.response_headers.items()
                if key.lower() in {"x-twmd-schema-ready", "x-twmd-coverage"}
            },
            "revision": _row_uniform(rows, "revision"),
            "capture_id": _row_uniform(rows, "capture_id"),
            "payload_sha256": _row_uniform(rows, "payload_sha256"),
        },
    )


def _flow_block(read) -> ResearchDataBlock:
    rows = [asdict(row) for row in read.data]
    coverage = [asdict(row) for row in read.coverage]
    receipts = [row.get("received_at_utc") for row in rows if row.get("received_at_utc")]
    receipts += [row.get("received_at_utc") or row.get("acquired_at") for row in coverage if row.get("received_at_utc") or row.get("acquired_at")]
    receipt = receipts[0] if receipts and all(value == receipts[0] for value in receipts) else "mixed" if receipts else None
    return ResearchDataBlock(
        data={"instrument_id": read.instrument_id, "native_unit": read.native_unit, "observations": rows},
        status=read.status,
        reason=read.reason,
        evidence={
            "provider": "twmd",
            "instrument_id": read.instrument_id,
            "endpoint": read.endpoint,
            "selectors": {"instrument_id": read.instrument_id, "start_date": read.start_date, "end_date": read.end_date},
            "source_contract": read.source_contract,
            "period": {"trade_dates": [row["trade_date"] for row in rows]},
            "source_report_date": None,
            "publication_time": None,
            "source_received_at_utc": receipt,
            "per_period_coverage": coverage,
            "per_period_provenance": rows,
            "served_at": None,
            "units": {"native_values": read.native_unit},
            "dataset_coverage": [row["status"] for row in coverage],
            "selected_instrument_presence": [row.get("selected_instrument_presence") for row in coverage],
            "revision": _row_uniform(rows, "revision"),
            "capture_id": _row_uniform(rows, "capture_id"),
            "payload_sha256": _row_uniform(rows, "payload_sha256"),
        },
    )


def _period_sort_key(value: object) -> date:
    if not isinstance(value, str):
        return date.min
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            return date.fromisoformat(f"{value}-01")
        return date.fromisoformat(value)
    except ValueError:
        return date.min


def _timestamp_age_seconds(value: object, now_utc: datetime) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    age = (now_utc - parsed.astimezone(timezone.utc)).total_seconds()
    return round(age, 3) if age >= 0 else None


def _period_age_days(value: object, now_utc: datetime) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = (int(part) for part in value.split("-"))
            period_end = date(year, month, calendar.monthrange(year, month)[1])
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            period_end = date.fromisoformat(value)
        else:
            return None
    except (ValueError, OverflowError):
        return None
    age = (now_utc.astimezone(_TAIPEI).date() - period_end).days
    return age if age >= 0 else None


def _freshness_observation(name: str, evidence: dict) -> dict:
    if name == "valuation":
        rows = evidence.get("per_period_provenance") or []
        row = max(rows, key=lambda item: _period_sort_key(item.get("trade_date")), default={})
        return {
            "data_period": row.get("trade_date"),
            "report_date": row.get("report_date"),
            "publication_time": row.get("publication_time", evidence.get("publication_time")),
            "source_received_at_utc": row.get("received_at_utc"),
            "first_observed_at": row.get("first_observed_at"),
        }
    if name == "institutional_flows":
        rows = evidence.get("per_period_provenance") or []
        row = max(rows, key=lambda item: _period_sort_key(item.get("trade_date")), default={})
        coverage = next(
            (item for item in evidence.get("per_period_coverage") or []
             if item.get("trade_date") == row.get("trade_date")),
            {},
        )
        return {
            "data_period": row.get("trade_date"),
            "report_date": row.get("report_date"),
            "publication_time": row.get("publication_time", evidence.get("publication_time")),
            "source_received_at_utc": (
                row.get("received_at_utc") or row.get("acquired_at")
                or coverage.get("received_at_utc") or coverage.get("acquired_at")
            ),
            "first_observed_at": row.get("first_observed_at"),
        }
    if name == "company_profile":
        retained = evidence.get("retained_profile") or {}
        snapshot = evidence.get("latest_snapshot") or {}
        return {
            "data_period": retained.get("report_date"),
            "report_date": retained.get("report_date"),
            "publication_time": evidence.get("publication_time"),
            "source_received_at_utc": retained.get("received_at_utc"),
            "first_observed_at": None,
            "latest_snapshot": {
                "report_date": snapshot.get("report_date"),
                "source_received_at_utc": snapshot.get("received_at_utc"),
            } if snapshot else None,
        }
    if name == "monthly_revenues":
        rows = [
            item for item in evidence.get("per_month_presence_and_provenance") or []
            if item.get("retained_row")
        ]
        item = max(rows, key=lambda entry: _period_sort_key(entry.get("data_month")), default={})
        row = item.get("retained_row") or {}
        data_month = item.get("data_month") or row.get("data_month")
        return {
            "data_period": data_month[:7] if isinstance(data_month, str) else None,
            "report_date": row.get("report_date"),
            "publication_time": evidence.get("publication_time"),
            "source_received_at_utc": row.get("received_at_utc"),
            "first_observed_at": None,
        }
    return {
        "data_period": None,
        "report_date": None,
        "publication_time": evidence.get("publication_time"),
        "source_received_at_utc": None,
        "first_observed_at": None,
    }


def _coverage_freshness(name: str, block: ResearchDataBlock) -> dict:
    evidence = block.evidence
    coverage = {
        "block_status": block.status,
        "dataset_coverage": evidence.get("dataset_coverage"),
        "selected_instrument_presence": evidence.get("selected_instrument_presence"),
        "requested_scope": dict(evidence.get("selectors") or {}),
        "calendar_assessed": False,
    }
    if name == "valuation":
        rows = evidence.get("per_period_provenance") or []
        coverage["observed_row_count"] = len(rows)
        coverage["source_coverage_header"] = evidence.get("dataset_coverage")
        coverage["interpretation"] = (
            "Selector dates are calendar bounds; this response does not establish which dates were trading sessions."
        )
    elif name == "institutional_flows":
        periods = evidence.get("per_period_coverage") or []
        coverage["reported_period_count"] = len(periods)
        coverage["reported_status_counts"] = {
            status: sum(1 for item in periods if item.get("status") == status)
            for status in sorted({item.get("status") for item in periods if item.get("status")})
        }
        presences = [item.get("selected_instrument_presence") for item in periods]
        coverage["selected_presence_counts"] = {
            presence: presences.count(presence)
            for presence in sorted({item for item in presences if item})
        }
        coverage["interpretation"] = (
            "Coverage follows source-reported dates; no exchange-calendar inference is applied, and selected-issuer absence is not zero flow."
        )
    elif name == "company_profile":
        coverage["snapshot_coverage_status"] = evidence.get("dataset_coverage")
        coverage["latest_snapshot_presence"] = evidence.get("selected_instrument_presence")
        coverage["interpretation"] = (
            "Latest whole-market snapshot coverage and retained issuer profile are separate observations."
        )
    elif name == "monthly_revenues":
        periods = evidence.get("per_month_presence_and_provenance") or []
        coverage["requested_month_count"] = len(periods)
        coverage["month_presence_counts"] = {
            presence: sum(1 for item in periods if item.get("presence") == presence)
            for presence in sorted({item.get("presence") for item in periods if item.get("presence")})
        }
        coverage["retained_row_count"] = sum(1 for item in periods if item.get("retained_row"))
        coverage["interpretation"] = (
            "Per-month presence is source evidence; no filing deadline, zero revenue, or authoritative empty result is inferred."
        )
    return coverage


def _freshness_metadata(
    name: str,
    block: ResearchDataBlock,
    evaluated_at_utc: datetime,
) -> dict:
    frequency, hint = _FRESHNESS_HINTS[name]
    observation = _freshness_observation(name, block.evidence)
    receipt = observation.get("source_received_at_utc")
    receipt_age = _timestamp_age_seconds(receipt, evaluated_at_utc)
    period_age = _period_age_days(observation.get("data_period"), evaluated_at_utc)
    metadata = {
        "frequency": frequency,
        "data_period": observation.get("data_period"),
        "report_date": observation.get("report_date"),
        "publication_time": observation.get("publication_time"),
        "source_received_at_utc": receipt,
        "source_receipt_age_seconds": receipt_age,
        "data_period_age_days": period_age,
        "first_observed_at": observation.get("first_observed_at"),
        "source_served_at": block.evidence.get("served_at"),
        "evaluated_at_utc": evaluated_at_utc.isoformat().replace("+00:00", "Z"),
        "publisher_sla": None,
        "age_status": "age_known_sla_unknown" if receipt_age is not None else "age_unknown",
        "frequency_hint": hint,
        "coverage": _coverage_freshness(name, block),
    }
    snapshot = observation.get("latest_snapshot")
    if snapshot is not None:
        snapshot_receipt_age = _timestamp_age_seconds(
            snapshot.get("source_received_at_utc"), evaluated_at_utc
        )
        metadata["latest_snapshot"] = {
            **snapshot,
            "source_receipt_age_seconds": snapshot_receipt_age,
            "data_period_age_days": _period_age_days(snapshot.get("report_date"), evaluated_at_utc),
        }
    return metadata


def _instrument_catalog(client: TwmdClient, config: dict) -> list[dict]:
    key = (*_scope(config), "catalog", "TW")
    cached = _cache_get(key)
    if cached is not None:
        return cached
    payload, _headers = client.get_response("instruments")
    if not isinstance(payload, list):
        raise TwmdReadError("twmd instruments response must be a list", reason_code="invalid_response")
    rows = []
    for row in payload:
        if not isinstance(row, dict):
            raise TwmdReadError("twmd instruments entries must be objects", reason_code="invalid_response")
        venue = row.get("venue")
        identity = row.get("instrument_id")
        symbol = row.get("symbol")
        security_type = row.get("security_type")
        active = row.get("is_active")
        if venue not in {"TWSE", "TPEX"} or not isinstance(identity, str) or not isinstance(symbol, str):
            continue
        if identity != f"{venue}:{symbol}" or security_type not in _CATALOG_SECURITY_TYPES or not isinstance(active, bool):
            raise TwmdReadError("twmd instruments response contains invalid canonical identity", reason_code="invalid_response")
        rows.append(row)
    _cache_set(key, rows, _CACHE_TTLS["catalog"])
    return rows


def _resolve(rows: list[dict], requested: str) -> tuple[str, dict]:
    try:
        parsed = Symbol.parse(requested, "TW")
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Taiwan instrument ID") from exc
    if parsed.market.value != "TW":
        raise ValueError("Taiwan research requires a Taiwan instrument")
    if parsed.venue:
        matches = [row for row in rows if row.get("instrument_id") == parsed.identity]
    else:
        matches = [row for row in rows if row.get("symbol") == parsed.code]
    active = [row for row in matches if row["is_active"]]
    selected = active or matches
    identities = {row["instrument_id"] for row in selected}
    if len(identities) > 1:
        raise ValueError(f"Taiwan symbol {parsed.code} needs an explicit TWSE:/TPEX: venue")
    if not selected:
        raise LookupError("instrument_not_found")
    row = selected[0]
    return row["instrument_id"], row


def _cache_key(config: dict, instrument_id: str, kind: str, selectors: tuple[str, ...]) -> tuple:
    return (*_scope(config), instrument_id, kind, *selectors)


def _status_error(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, TwmdReadError):
        status = exc.status_code if type(exc.status_code) is int and 100 <= exc.status_code <= 599 else None
        reason = exc.reason_code or (f"http_{status}" if status else "provider_error")
        if reason in {"invalid_response", "transport_error", "timeout", "provider_error"}:
            return reason, status
        if re.fullmatch(r"http_\d{3}", reason):
            parsed_status = int(reason[-3:])
            if 100 <= parsed_status <= 599:
                return reason, status or parsed_status
        return (f"http_{status}" if status else "provider_error"), status
    if isinstance(exc, RuntimeError) and str(exc) == "concurrency_limit":
        return "concurrency_limit", None
    if isinstance(exc, TimeoutError):
        return "timeout", None
    if isinstance(exc, LookupError) and str(exc) == "instrument_not_found":
        return "instrument_not_found", None
    if isinstance(exc, (TypeError, ValueError)):
        return "invalid_response", None
    return "provider_error", None


def _safe_block_error_reason(reason: str, http_status: object) -> str:
    status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
    if reason in {
        "invalid_response", "transport_error", "timeout", "provider_error",
        "concurrency_limit", "instrument_not_found", "ambiguous_instrument",
        "invalid_instrument_id", "instrument_inactive",
    }:
        return reason
    if re.fullmatch(r"http_\d{3}", reason):
        code = int(reason[-3:])
        if 100 <= code <= 599:
            return reason
    return f"http_{status}" if status else "provider_error"


class TaiwanResearchService:
    """Merge four independent official TWMD reads with bounded scope and fan-out."""

    def __init__(self, *, client: TwmdClient | None = None, config: dict | None = None):
        self.config = dict(config if config is not None else twmd_config())
        self.client = client or _BoundedTwmdClient(self.config)

    def collect(
        self,
        instrument_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        start_month: str | None = None,
        end_month: str | None = None,
        today_taipei: date | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
        request_clock = now_utc or datetime.now(timezone.utc)
        if request_clock.tzinfo is None or request_clock.utcoffset() is None:
            raise ValueError("now_utc must be timezone-aware")
        request_clock = request_clock.astimezone(timezone.utc)
        today = today_taipei or request_clock.astimezone(_TAIPEI).date()
        date_start, date_end, month_start, month_end = _bounds(
            today=today,
            start_date=start_date,
            end_date=end_date,
            start_month=start_month,
            end_month=end_month,
        )
        selectors = {
            "start_date": date_start.isoformat(),
            "end_date": date_end.isoformat(),
            "start_month": month_start.strftime("%Y-%m"),
            "end_month": month_end.strftime("%Y-%m"),
        }
        template = {
            "valuation": ("/api/v1/valuations", {"instrument_id": instrument_id, "start": selectors["start_date"], "end": selectors["end_date"]}),
            "institutional_flows": ("/api/v1/institutional-flows", {"instrument_id": instrument_id, "start_date": selectors["start_date"], "end_date": selectors["end_date"]}),
            "company_profile": ("/api/v1/company-profiles", {"instrument_id": instrument_id}),
            "monthly_revenues": ("/api/v1/monthly-revenues", {"instrument_id": instrument_id, "start_month": selectors["start_month"], "end_month": selectors["end_month"]}),
        }
        if not _REQUEST_SLOTS.acquire(blocking=False):
            blocks = {
                name: _error_block("concurrency_limit", endpoint, block_selectors, self.config, instrument_id=instrument_id)
                for name, (endpoint, block_selectors) in template.items()
            }
            return self._result(instrument_id, None, selectors, blocks, request_clock)

        try:
            catalog_future = None
            try:
                rows = _cache_get((*_scope(self.config), "catalog", "TW"))
                if rows is None:
                    catalog_future = _submit_read(_instrument_catalog, self.client, self.config)
                    rows = catalog_future.result(timeout=max(0.0, deadline - time.monotonic()))
                canonical, instrument = _resolve(rows, instrument_id)
            except Exception as exc:
                if catalog_future is not None:
                    catalog_future.cancel()
                reason, status = _status_error(exc)
                if reason == "instrument_not_found":
                    status_name, block_status = "instrument_not_found", "unsupported"
                elif reason == "concurrency_limit":
                    status_name, block_status = reason, "error"
                elif isinstance(exc, ValueError):
                    status_name, block_status = (
                        "ambiguous_instrument" if "needs an explicit" in str(exc)
                        else "invalid_instrument_id"
                    ), "error"
                else:
                    status_name, block_status = reason, "error"
                blocks = {
                    name: _error_block(status_name, endpoint, block_selectors, self.config,
                                       status=block_status, instrument_id=instrument_id,
                                       http_status=status)
                    for name, (endpoint, block_selectors) in template.items()
                }
                return self._result(instrument_id, None, selectors, blocks, request_clock)

            if time.monotonic() >= deadline:
                blocks = {
                    name: _error_block("timeout", endpoint, block_selectors, self.config,
                                       instrument_id=canonical)
                    for name, (endpoint, block_selectors) in template.items()
                }
                return self._result(canonical, instrument, selectors, blocks, request_clock)

            canonical_template = {
                name: (endpoint, {**block_selectors, "instrument_id": canonical})
                for name, (endpoint, block_selectors) in template.items()
            }
            if not instrument["is_active"]:
                blocks = {
                    name: _error_block("instrument_inactive", endpoint, block_selectors,
                                       self.config, status="unsupported", instrument_id=canonical)
                    for name, (endpoint, block_selectors) in canonical_template.items()
                }
                return self._result(canonical, instrument, selectors, blocks, request_clock)
            if instrument["security_type"] not in {"EQUITY", "ETF"}:
                reason = "unsupported_warrant" if instrument["security_type"] == "WARRANT" else "unsupported_security_type"
                blocks = {
                    name: _error_block(reason, endpoint, block_selectors, self.config,
                                       status="unsupported", instrument_id=canonical)
                    for name, (endpoint, block_selectors) in canonical_template.items()
                }
                return self._result(canonical, instrument, selectors, blocks, request_clock)

            blocks: dict[str, ResearchDataBlock] = {}
            builders = {
                "valuation": lambda: _valuation_block(self.client.valuation_history(
                    canonical, date_start, date_end, today_taipei=today
                )),
                "institutional_flows": lambda: _flow_block(self.client.institutional_flows(
                    canonical, date_start, date_end, today_taipei=today
                )),
                "company_profile": lambda: company_profile_block(self.client.company_profile(canonical)),
                "monthly_revenues": lambda: monthly_revenue_block(self.client.monthly_revenues(
                    canonical, selectors["start_month"], selectors["end_month"], today_taipei=today
                )),
            }
            tpex_code = canonical.split(":", 1)[1] if canonical.startswith("TPEX:") else ""
            if tpex_code and not re.fullmatch(r"\d{4}", tpex_code):
                endpoint, block_selectors = canonical_template["valuation"]
                blocks["valuation"] = _error_block(
                    "unsupported_valuation_selector", endpoint, block_selectors,
                    self.config, status="unsupported", instrument_id=canonical,
                )
                del builders["valuation"]
            names = list(builders)
            futures = {}
            for name in names:
                endpoint, block_selectors = canonical_template[name]
                key = _cache_key(self.config, canonical, name, tuple(block_selectors.values()))
                cached = _cache_get(key)
                if cached is not None:
                    blocks[name] = cached
                    continue
                try:
                    future = _submit_read(self._load_block, name, key, builders[name])
                except RuntimeError:
                    blocks[name] = _error_block(
                        "concurrency_limit", endpoint, block_selectors, self.config,
                        instrument_id=canonical,
                    )
                    continue
                futures[name] = future
            remaining = max(0.0, deadline - time.monotonic())
            _done, pending = wait(futures.values(), timeout=remaining)
            pending_set = set(pending)
            for name, future in futures.items():
                if future in pending_set:
                    endpoint, block_selectors = canonical_template[name]
                    future.cancel()
                    blocks[name] = _error_block(
                        "timeout", endpoint, block_selectors, self.config,
                        instrument_id=canonical,
                    )
                    continue
                try:
                    blocks[name] = future.result()
                except Exception as exc:  # each independent block remains isolated
                    endpoint, block_selectors = canonical_template[name]
                    reason, status = _status_error(exc)
                    blocks[name] = _error_block(
                        reason, endpoint, block_selectors, self.config,
                        instrument_id=canonical, http_status=status,
                    )
            return self._result(canonical, instrument, selectors, blocks, request_clock)
        finally:
            _REQUEST_SLOTS.release()

    def _load_block(self, name: str, key: tuple, builder) -> ResearchDataBlock:
        block = builder()
        if block.status == "error":
            evidence = dict(block.evidence)
            status = evidence.get("http_status")
            evidence["http_status"] = status if type(status) is int and 100 <= status <= 599 else None
            block = ResearchDataBlock(
                data=block.data,
                status=block.status,
                reason=_safe_block_error_reason(block.reason, status),
                evidence=evidence,
            )
        if block.status in {"available", "partial", "missing", "absent", "empty", "unsupported", "unknown"}:
            _cache_set(key, block, _CACHE_TTLS[name])
        return block

    @staticmethod
    def _result(
        instrument_id: str,
        instrument: dict | None,
        selectors: dict[str, str],
        blocks: dict[str, ResearchDataBlock],
        evaluated_at_utc: datetime,
    ) -> dict[str, Any]:
        serialized_blocks = {}
        for name, block in blocks.items():
            evidence = copy.deepcopy(block.evidence)
            evidence["freshness"] = _freshness_metadata(name, block, evaluated_at_utc)
            serialized_blocks[name] = asdict(ResearchDataBlock(
                data=block.data,
                status=block.status,
                reason=block.reason,
                evidence=evidence,
            ))
        return {
            "instrument_id": instrument_id,
            "instrument": ({
                "venue": instrument["venue"],
                "symbol": instrument["symbol"],
                "security_type": instrument["security_type"],
                "is_active": instrument["is_active"],
                "name": instrument.get("name"),
            } if instrument else None),
            "selectors": dict(selectors),
            "blocks": serialized_blocks,
            "limitations": {
                "financial_statements": {
                    "status": "not_integrated",
                    "data": None,
                    "message": (
                        "Complete Taiwan financial statements are not integrated. "
                        "Monthly revenue and quotes are not income, balance-sheet, "
                        "or cash-flow statements."
                    ),
                }
            },
        }


def get_taiwan_research_service() -> TaiwanResearchService:
    return TaiwanResearchService()


def serialize_taiwan_research(payload: dict[str, Any]) -> dict[str, Any]:
    """Defensive JSON-safe copy for API and assistant result boundaries."""
    return copy.deepcopy(payload)
