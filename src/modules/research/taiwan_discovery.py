"""Bounded opt-in discovery over official Taiwan issuer research reads."""

from __future__ import annotations

import copy
import calendar
import hashlib
import threading
import time
from dataclasses import asdict, is_dataclass
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from marketdata.errors import TwmdReadError
from marketdata.vendors.twmd import TwmdClient

from src.modules.research.taiwan_research import _BoundedTwmdClient
from src.platform.marketdata.marketdata_client import get_market_data

_TAIPEI = ZoneInfo("Asia/Taipei")
_MAX_CANDIDATES = 20
_PRICE_UNIVERSE_CAP = 2_000
_MAX_DAILY_AGE_DAYS = 7
_MAX_REVENUE_AGE_DAYS = 90
_REQUEST_DEADLINE_SECONDS = 30.0
_CACHE_TTL_SECONDS = 300.0
_CACHE_LIMIT = 512
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
_SCREEN_REQUEST_SLOTS = threading.BoundedSemaphore(2)
_SCREEN_READ_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tw-discovery-read")
_REQUEST_COUNTS_LOCK = threading.Lock()

_CONDITION_META = {
    "pe_max": {"label": "本益比上限", "unit": "倍", "dataset": "valuation"},
    "pb_max": {"label": "股價淨值比上限", "unit": "倍", "dataset": "valuation"},
    "dividend_yield_min_pct": {"label": "殖利率下限", "unit": "%", "dataset": "valuation"},
    "revenue_yoy_min_pct": {"label": "營收年增率下限", "unit": "%", "dataset": "monthly_revenue"},
    "institutional_net_min_shares": {"label": "單日法人買賣超下限", "unit": "股", "dataset": "institutional_flows"},
}


def _previous_month(today: date) -> date:
    return date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)


def _month_start(today: date, offset: int) -> date:
    current = _previous_month(today)
    index = current.year * 12 + current.month - 1 - offset
    return date(index // 12, index % 12 + 1, 1)


def _daily_bounds(today: date) -> tuple[date, date]:
    end = today - timedelta(days=1)
    return max(date(2024, 1, 1), end - timedelta(days=29)), end


def _month_bounds(today: date) -> tuple[str, str]:
    start = _month_start(today, 11)
    end = _previous_month(today)
    return start.strftime("%Y-%m"), end.strftime("%Y-%m")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _client_scope(client: TwmdClient) -> tuple[str, ...]:
    token_hash = hashlib.sha256(str(client.config.get("token") or "").encode()).hexdigest()
    try:
        parsed = urlsplit(client.base_url)
        host = parsed.hostname
        if not host:
            public_scope = "configured_twmd_service"
        else:
            host = f"[{host}]" if ":" in host else host
            port = f":{parsed.port}" if parsed.port is not None else ""
            public_scope = f"{parsed.scheme or 'http'}://{host}{port}"
    except ValueError:
        public_scope = "configured_twmd_service"
    endpoint_hash = hashlib.sha256(str(client.base_url).encode()).hexdigest()
    return public_scope, endpoint_hash, token_hash


def _cached_read(key: tuple, loader):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            expires, value = hit
            if expires > now:
                _CACHE.move_to_end(key)
                return copy.deepcopy(value), True
            del _CACHE[key]
    on_attempt = getattr(loader, "on_attempt", None)
    if on_attempt is not None:
        on_attempt()
    value = loader()
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, copy.deepcopy(value))
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return value, False


class _ScreenLease:
    """Keep the screen-request permit until all submitted work has stopped."""

    def __init__(self):
        self._lock = threading.Lock()
        self._references = 1
        self._released = False

    def hold(self):
        with self._lock:
            self._references += 1

    def release(self):
        with self._lock:
            self._references -= 1
            if self._references or self._released:
                return
            self._released = True
        _SCREEN_REQUEST_SLOTS.release()


class _DeadlineBoundedTwmdClient(_BoundedTwmdClient):
    """Clamp each official read to both the configured timeout and screen deadline."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._deadline_local = threading.local()

    def get_response(self, path: str, *, timeout_sec=None, retries=None, **params):
        deadline = getattr(self._deadline_local, "deadline", None)
        remaining = deadline - time.monotonic() if deadline is not None else None
        configured = timeout_sec
        if configured is None:
            configured = self.config.get("timeout_sec") or 5
        try:
            timeout = min(float(configured), 20.0)
        except (TypeError, ValueError):
            timeout = 5.0
        if remaining is not None:
            timeout = min(timeout, remaining)
            if timeout <= 0:
                raise TimeoutError("discovery request deadline exceeded")
        self._deadline_local.transport_attempts = (
            getattr(self._deadline_local, "transport_attempts", 0) + 1
        )
        on_transport_attempt = getattr(self._deadline_local, "on_transport_attempt", None)
        if on_transport_attempt is not None:
            on_transport_attempt()
        return super().get_response(path, timeout_sec=timeout, retries=0, **params)

    def set_deadline(self, deadline: float, on_transport_attempt=None) -> None:
        self._deadline_local.deadline = deadline
        self._deadline_local.transport_attempts = 0
        self._deadline_local.on_transport_attempt = on_transport_attempt

    def clear_deadline(self) -> None:
        self._deadline_local.deadline = None
        self._deadline_local.on_transport_attempt = None

    def transport_attempt_count(self) -> int:
        return getattr(self._deadline_local, "transport_attempts", 0)


def clear_taiwan_discovery_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, TwmdReadError):
        return exc.reason_code or (f"http_{exc.status_code}" if exc.status_code else "provider_error")
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "provider_error"


class TaiwanDiscoveryService:
    """Screen only a capped, price-ranked candidate set using explicit TWMD reads."""

    def __init__(self, *, market_data=None):
        self.market_data = market_data or get_market_data()

    def collect(
        self,
        conditions: dict[str, object],
        *,
        limit: int = _MAX_CANDIDATES,
        today_taipei: date | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_conditions(conditions)
        result_limit = self._validate_limit(limit)
        request_clock = now_utc or datetime.now(timezone.utc)
        if request_clock.tzinfo is None or request_clock.utcoffset() is None:
            raise ValueError("now_utc must be timezone-aware")
        request_clock = request_clock.astimezone(timezone.utc)
        today = today_taipei or request_clock.astimezone(_TAIPEI).date()
        deadline = time.monotonic() + _REQUEST_DEADLINE_SECONDS
        start_date, end_date = _daily_bounds(today)
        start_month, end_month = _month_bounds(today)
        selectors = {
            "daily_start_date": start_date.isoformat(),
            "daily_end_date": end_date.isoformat(),
            "revenue_start_month": start_month,
            "revenue_end_month": end_month,
            "today_taipei": today.isoformat(),
        }
        source_clients, source_errors = self._read_clients(normalized)
        counts = {
            "catalog": 0, "price_snapshots": 0, "valuation": 0,
            "monthly_revenue": 0, "institutional_flows": 0,
            "valuation_http": 0, "monthly_revenue_http": 0,
            "institutional_flows_http": 0,
            "cache_hits": 0, "failed_requests": 0,
        }

        if not _SCREEN_REQUEST_SLOTS.acquire(blocking=False):
            return self._empty_result(
                normalized, selectors, today, result_limit, "discovery_concurrency_limit",
                source_errors, counts, pool=None, evaluated_at_utc=request_clock,
            )
        lease = _ScreenLease()
        try:
            try:
                pool = self.market_data.taiwan_discovery_pool(
                    mode="turnover", limit=_MAX_CANDIDATES,
                    max_universe_size=_PRICE_UNIVERSE_CAP,
                    deadline_monotonic=deadline,
                )
            except Exception as exc:
                counts["failed_requests"] += 1
                return self._empty_result(
                    normalized, selectors, today, result_limit, _error_reason(exc),
                    source_errors, counts, pool=None, evaluated_at_utc=request_clock,
                )
            counts["catalog"] += pool.catalog_request_count
            counts["price_snapshots"] += pool.price_snapshot_request_count
            counts["cache_hits"] += pool.cache_hits
            counts["failed_requests"] += pool.failed_request_count

            if pool.status == "quote_source_disabled":
                return self._empty_result(
                    normalized, selectors, today, result_limit, "quote_source_disabled",
                    source_errors, counts, pool=pool, evaluated_at_utc=request_clock,
                )

            candidates = list(pool.items[:_MAX_CANDIDATES])
            if not candidates:
                status = pool.status or "no_current_eligible_prices"
                return self._empty_result(
                    normalized, selectors, today, result_limit, status,
                    source_errors, counts, pool=pool, evaluated_at_utc=request_clock,
                )

            candidate_results = self._evaluate_candidates(
                candidates, normalized, source_clients, source_errors, selectors,
                today, request_clock, counts, deadline, lease,
                pool.security_type_by_instrument_id,
            )
            factor_failures = sum(item.pop("_request_failure_count", 0) for item in candidate_results)
            counts["failed_requests"] += factor_failures
            candidate_results.sort(
                key=lambda item: (
                    not item["matched"],
                    -(_decimal(item.get("price", {}).get("turnover")) or Decimal(0)),
                    item["instrument_id"],
                )
            )
            matches = [item for item in candidate_results if item["matched"]]
            excluded = [item for item in candidate_results if not item["matched"]]
            partial_reasons: list[str] = []
            if pool.partial_scan:
                partial_reasons.append("price_pool_incomplete_or_capped")
            if pool.ranked_price_count > len(candidates):
                partial_reasons.append("candidate_cap_reached")
            timed_out_count = sum(
                "screen_deadline_exceeded" in item.get("exclusion_reasons", [])
                for item in candidate_results
            )
            if timed_out_count:
                partial_reasons.append("candidate_reads_incomplete")
            if factor_failures:
                partial_reasons.append("candidate_data_reads_failed")
            if source_errors:
                partial_reasons.append("official_factor_source_unavailable")

            price_dates = list(pool.price_data_dates)
            research_dates = {
                "valuation": sorted({item["data_dates"].get("valuation") for item in candidate_results if item["data_dates"].get("valuation")}),
                "monthly_revenue": sorted({item["data_dates"].get("monthly_revenue") for item in candidate_results if item["data_dates"].get("monthly_revenue")}),
                "institutional_flows": sorted({item["data_dates"].get("institutional_flows") for item in candidate_results if item["data_dates"].get("institutional_flows")}),
            }
            scope = self._scope(pool, len(candidates), selectors, partial_reasons)
            scope.update({
                "price_data_dates": price_dates,
                "research_data_dates": research_dates,
                "candidates_examined": len(candidate_results) - timed_out_count,
                "candidates_evaluated": len(candidate_results) - timed_out_count,
                "candidates_timed_out": timed_out_count,
                "matched_count": len(matches),
                "excluded_count": len(excluded),
                "returned_count": min(len(matches), result_limit),
                "results_truncated": len(matches) > result_limit,
                "request_counts": self._request_counts(counts),
                "source_status": source_errors,
                "scan_status": "partial" if partial_reasons else "available",
                "evaluated_at_utc": request_clock.isoformat().replace("+00:00", "Z"),
            })
            return {
                "market": "TW",
                "provider": "twmd_official",
                "selectors": selectors,
                "conditions": self._condition_descriptions(normalized),
                "scope": scope,
                "matches": matches[:result_limit],
                "excluded": excluded,
            }
        finally:
            lease.release()

    @staticmethod
    def _validate_conditions(conditions: dict[str, object]) -> dict[str, object]:
        if not isinstance(conditions, dict):
            raise ValueError("conditions must be an object")
        unknown = set(conditions).difference(_CONDITION_META)
        if unknown:
            raise ValueError(f"unsupported discovery condition: {sorted(unknown)[0]}")
        normalized: dict[str, object] = {}
        for name, value in conditions.items():
            if value is None:
                continue
            if name == "institutional_net_min_shares":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"{name} must be an integer")
                if not -10_000_000_000 <= value <= 10_000_000_000:
                    raise ValueError(f"{name} must be between -10000000000 and 10000000000")
                normalized[name] = value
                continue
            number = _decimal(value)
            if number is None:
                raise ValueError(f"{name} must be a finite number")
            if name in {"pe_max", "pb_max"} and not 0 < number <= 1000:
                raise ValueError(f"{name} must be greater than 0 and at most 1000")
            if name == "dividend_yield_min_pct" and not 0 <= number <= 1000:
                raise ValueError(f"{name} must be between 0 and 1000")
            if name == "revenue_yoy_min_pct" and not -1000 <= number <= 100000:
                raise ValueError(f"{name} must be between -1000 and 100000")
            normalized[name] = str(number)
        if not normalized:
            raise ValueError("select at least one official discovery condition")
        return normalized

    @staticmethod
    def _validate_limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_CANDIDATES:
            raise ValueError(f"limit must be between 1 and {_MAX_CANDIDATES}")
        return limit

    def _read_clients(self, conditions: dict[str, object]):
        requested = {
            "valuation": "fundamentals",
            "monthly_revenue": "monthly_revenue",
            "institutional_flows": "capital_flow",
        }
        source_clients = {}
        source_errors = {}
        needed = {_CONDITION_META[name]["dataset"] for name in conditions}
        for dataset in sorted(needed):
            try:
                client = self.market_data._twmd_research_client(requested[dataset])
                if isinstance(client, TwmdClient):
                    client = _DeadlineBoundedTwmdClient(dict(client.config))
                source_clients[dataset] = client
            except Exception:
                source_errors[dataset] = "official_source_not_configured"
        return source_clients, source_errors

    def _evaluate_candidates(
        self, candidates, conditions, source_clients, source_errors, selectors,
        today, request_clock, counts, deadline, lease, security_types,
    ):
        tasks = {}
        results = []
        for stock in candidates:
            if time.monotonic() >= deadline:
                results.append(self._unread_candidate(
                    stock.symbol, "screen_deadline_exceeded",
                    security_types.get(stock.symbol, "unknown"), conditions,
                ))
                continue
            lease.hold()
            try:
                future = _SCREEN_READ_POOL.submit(
                    self._evaluate_one, stock, conditions, source_clients, source_errors,
                    selectors, today, request_clock, counts, deadline,
                    pool_security_type=security_types.get(stock.symbol, "unknown"),
                )
            except Exception:
                lease.release()
                results.append(self._unread_candidate(
                    stock.symbol, "candidate_evaluation_error",
                    security_types.get(stock.symbol, "unknown"), conditions,
                ))
                continue
            future.add_done_callback(lambda _done: lease.release())
            tasks[future] = stock.symbol
        done, pending = wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        for future in pending:
            future.cancel()
        for future in done:
            try:
                results.append(future.result())
            except Exception:
                instrument_id = tasks[future]
                results.append(self._unread_candidate(
                    instrument_id, "candidate_evaluation_error",
                    security_types.get(instrument_id, "unknown"), conditions,
                ))
        results.extend(
            self._unread_candidate(
                tasks[future], "screen_deadline_exceeded",
                security_types.get(tasks[future], "unknown"),
                conditions,
            )
            for future in pending
        )
        return results

    def _evaluate_one(
        self, stock, conditions, source_clients, source_errors, selectors,
        today, request_clock, counts, deadline, *, pool_security_type,
    ):
        instrument_id = stock.symbol
        venue, symbol = instrument_id.split(":", 1)
        data: dict[str, Any] = {}
        data_dates: dict[str, str | None] = {
            "valuation": None, "monthly_revenue": None, "institutional_flows": None,
        }
        errors: dict[str, str] = {}
        datasets = sorted({_CONDITION_META[name]["dataset"] for name in conditions})
        request_failure_count = 0
        for dataset in datasets:
            if dataset in source_errors:
                errors[dataset] = source_errors[dataset]
                continue
            if dataset == "monthly_revenue" and self._is_etf(pool_security_type):
                errors[dataset] = "unsupported_etf"
                continue
            if dataset == "valuation" and venue == "TPEX" and (len(symbol) != 4 or not symbol.isdigit()):
                errors[dataset] = "unsupported_valuation_selector"
                continue
            if time.monotonic() >= deadline:
                errors[dataset] = "screen_deadline_exceeded"
                continue
            client = source_clients[dataset]
            if dataset == "valuation":
                selectors_for_read = (selectors["daily_start_date"], selectors["daily_end_date"])
                loader = lambda c=client, i=instrument_id, bounds=selectors_for_read: c.valuation_history(
                    i, bounds[0], bounds[1], today_taipei=today,
                )
            elif dataset == "monthly_revenue":
                selectors_for_read = (selectors["revenue_start_month"], selectors["revenue_end_month"])
                loader = lambda c=client, i=instrument_id, bounds=selectors_for_read: c.monthly_revenues(
                    i, bounds[0], bounds[1], today_taipei=today,
                )
            else:
                selectors_for_read = (selectors["daily_start_date"], selectors["daily_end_date"])
                loader = lambda c=client, i=instrument_id, bounds=selectors_for_read: c.institutional_flows(
                    i, bounds[0], bounds[1], today_taipei=today,
                )
            key = (*_client_scope(client), instrument_id, dataset, *selectors_for_read)
            read_started = False

            def mark_read_started(dataset_name=dataset):
                nonlocal read_started
                read_started = True
                self._increment(counts, dataset_name)

            if isinstance(client, _DeadlineBoundedTwmdClient):
                client.set_deadline(
                    deadline,
                    on_transport_attempt=lambda d=dataset: self._increment(counts, f"{d}_http"),
                )
            loader.on_attempt = mark_read_started
            try:
                value, cache_hit = _cached_read(key, loader)
                if cache_hit:
                    self._increment(counts, "cache_hits")
                elif (
                    read_started
                    and dataset == "monthly_revenue"
                    and isinstance(client, _DeadlineBoundedTwmdClient)
                    and client.transport_attempt_count() == 0
                ):
                    # TwmdClient has a longer-lived typed monthly read cache.
                    self._increment(counts, "cache_hits")
                data[dataset] = value
            except Exception as exc:
                request_failure_count += 1
                errors[dataset] = _error_reason(exc)
            finally:
                if isinstance(client, _DeadlineBoundedTwmdClient):
                    client.clear_deadline()

        observations = {}
        if "valuation" in data:
            rows = list(data["valuation"].data)
            latest = max(rows, key=lambda row: row.trade_date) if rows else None
            observations["valuation"] = latest
            data_dates["valuation"] = latest.trade_date if latest else None
        if "monthly_revenue" in data:
            rows = [
                item.row for item in data["monthly_revenue"].months
                if item.presence == "present" and item.row is not None
            ]
            latest = max(rows, key=lambda row: row.data_month) if rows else None
            observations["monthly_revenue"] = latest
            data_dates["monthly_revenue"] = latest.data_month[:7] if latest else None
        if "institutional_flows" in data:
            rows = list(data["institutional_flows"].data)
            latest = max(rows, key=lambda row: row.trade_date) if rows else None
            observations["institutional_flows"] = latest
            data_dates["institutional_flows"] = latest.trade_date if latest else None

        data_evidence = {
            dataset: self._observation_evidence(
                dataset, data.get(dataset), observations.get(dataset), errors.get(dataset),
            )
            for dataset in datasets
        }

        condition_results = {}
        explanations = []
        matched = True
        for name, threshold in conditions.items():
            dataset = _CONDITION_META[name]["dataset"]
            observation = observations.get(dataset)
            result = self._condition_result(
                name, threshold, observation, errors.get(dataset), data.get(dataset), today,
            )
            condition_results[name] = result
            if result["passed"]:
                explanations.append(result["explanation"])
            else:
                matched = False

        security_type = pool_security_type
        return {
            "instrument_id": instrument_id,
            "symbol": symbol,
            "venue": venue,
            "market": "TW",
            "security_type": security_type,
            "name": stock.name,
            "matched": matched,
            "price": {
                "turnover": stock.turnover,
                "change_pct": stock.change_pct,
                "trade_date": stock.trade_date,
            },
            "values": {
                "pe_ratio": getattr(observations.get("valuation"), "pe_ratio", None),
                "pb_ratio": getattr(observations.get("valuation"), "pb_ratio", None),
                "dividend_yield_pct": getattr(observations.get("valuation"), "dividend_yield_pct", None),
                "revenue_yoy_pct": getattr(observations.get("monthly_revenue"), "year_over_year_pct", None),
                "institutional_net_shares": (getattr(observations.get("institutional_flows"), "native_values", {}) or {}).get("total_institutional_net_shares"),
            },
            "data_dates": data_dates,
            "data_evidence": data_evidence,
            "condition_results": condition_results,
            "explanations": explanations if matched else [
                result["explanation"] for result in condition_results.values()
            ],
            "exclusion_reasons": [
                result["reason"] for result in condition_results.values() if not result["passed"]
            ],
            "_request_failure_count": request_failure_count,
        }

    @staticmethod
    def _is_etf(security_type: str) -> bool:
        # The pool already preflights catalog types. ETF revenue is explicitly
        # unsupported by the upstream issuer-revenue contract.
        return security_type == "ETF"

    @staticmethod
    def _increment(counts: dict[str, int], key: str) -> None:
        with _REQUEST_COUNTS_LOCK:
            counts[key] += 1

    def _condition_result(self, name, threshold, observation, error, read, today):
        meta = _CONDITION_META[name]
        threshold_text = str(threshold)
        result = {
            "condition": name,
            "label": meta["label"],
            "operator": "<=" if name in {"pe_max", "pb_max"} else ">=",
            "threshold": threshold_text,
            "unit": meta["unit"],
            "value": None,
            "data_date": None,
            "passed": False,
            "reason": None,
            "explanation": "",
        }
        if error:
            result["reason"] = error
            result["explanation"] = f"{meta['label']}無法判定：{error}。"
            return result
        if observation is None:
            result["reason"] = getattr(read, "reason", None) or "coverage_missing"
            result["explanation"] = f"{meta['label']}資料缺漏或未覆蓋，條件未通過。"
            return result

        if name in {"pe_max", "pb_max", "dividend_yield_min_pct"}:
            field = {"pe_max": "pe_ratio", "pb_max": "pb_ratio", "dividend_yield_min_pct": "dividend_yield_pct"}[name]
            value = getattr(observation, field, None)
            data_date = observation.trade_date
            observed_date = _date(data_date)
            if observed_date is None:
                result.update(reason="invalid_data_date", data_date=data_date)
                result["explanation"] = f"{meta['label']}資料日期無法確認，條件未通過。"
                return result
            age = (today - observed_date).days
            if age > _MAX_DAILY_AGE_DAYS:
                result.update(reason="stale", data_date=data_date)
                result["explanation"] = f"{meta['label']}最新資料日 {data_date} 已超過 {_MAX_DAILY_AGE_DAYS} 個日曆日。"
                return result
        elif name == "revenue_yoy_min_pct":
            value = getattr(observation, "year_over_year_pct", None)
            data_date = observation.data_month[:7]
            month = _date(observation.data_month)
            if month is not None:
                month_end = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
                age = (today - month_end).days
                if age > _MAX_REVENUE_AGE_DAYS:
                    result.update(reason="stale", data_date=data_date)
                    result["explanation"] = f"營收期別 {data_date} 已超過 {_MAX_REVENUE_AGE_DAYS} 日曆日。"
                    return result
        else:
            native = getattr(observation, "native_values", {}) or {}
            value = native.get("total_institutional_net_shares")
            data_date = observation.trade_date
            observed_date = _date(data_date)
            if observed_date is None:
                result.update(reason="invalid_data_date", data_date=data_date)
                result["explanation"] = f"法人資料日期無法確認，條件未通過。"
                return result
            age = (today - observed_date).days
            if age > _MAX_DAILY_AGE_DAYS:
                result.update(reason="stale", data_date=data_date)
                result["explanation"] = f"法人資料日 {data_date} 已超過 {_MAX_DAILY_AGE_DAYS} 個日曆日。"
                return result

        result.update(value=value, data_date=data_date)
        observed = _decimal(value)
        limit = _decimal(threshold)
        if observed is None:
            result["reason"] = "value_missing"
            result["explanation"] = f"{meta['label']}在 {data_date} 沒有有效數值，條件未通過。"
            return result
        passed = observed <= limit if name in {"pe_max", "pb_max"} else observed >= limit
        result["passed"] = passed
        result["reason"] = "matched" if passed else "condition_not_met"
        result["explanation"] = (
            f"{meta['label']} {observed} {meta['unit']}（資料日 {data_date}）"
            f"{'符合' if passed else '未達'} {result['operator']} {limit} {meta['unit']}。"
        )
        return result

    @staticmethod
    def _observation_evidence(dataset, read, observation, error):
        """Keep selected-row lineage and source coverage alongside the value."""
        evidence = {
            "source_contract": None,
            "source_url": None,
            "source_received_at_utc": None,
            "acquired_at": None,
            "first_observed_at": None,
            "publication_time": None,
            "source_served_at": getattr(read, "served_at", None),
            "report_date": None,
            "capture_id": None,
            "revision": None,
            "units": {},
            "coverage": None,
            "presence": "unknown",
            "payload_sha256": None,
            "error": error,
        }
        if read is None:
            return evidence

        coverage = None
        if dataset == "valuation":
            evidence.update({
                "endpoint": getattr(read, "endpoint", None),
                "schema_ready": getattr(read, "schema_ready", None),
                "coverage": getattr(read, "coverage_header", None),
                "presence": getattr(read, "selected_instrument_presence", "unknown"),
                "read_status": getattr(read, "status", None),
                "read_reason": getattr(read, "reason", None),
                "units": {
                    "pe_ratio": "ratio",
                    "pb_ratio": "ratio",
                    "dividend_yield_pct": "percent",
                    "dividend_per_share": "source currency per share",
                },
            })
            if observation is not None:
                evidence.update({
                    "source_contract": observation.source_contract,
                    "source_url": observation.source_url,
                    "source_received_at_utc": observation.received_at_utc,
                    "capture_id": observation.capture_id,
                    "revision": observation.revision,
                    "payload_sha256": observation.payload_sha256,
                    "request_scope": observation.request_scope,
                    "dividend_reference_year": observation.dividend_reference_year,
                    "financial_reference_year": observation.financial_reference_year,
                    "financial_reference_quarter": observation.financial_reference_quarter,
                    "dividend_per_share_currency": getattr(observation, "dividend_per_share_currency", None),
                    "dividend_yield_interpretation": "provider value; dividend reference year is shown; not a current annualized yield inference",
                })
        elif dataset == "monthly_revenue":
            evidence.update({
                "endpoint": getattr(read, "endpoint", None),
                "dataset": getattr(read, "dataset", None),
                "schema_ready": getattr(read, "schema_ready", None),
                "read_status": getattr(read, "status", None),
                "read_reason": getattr(read, "reason", None),
                "coverage_status": getattr(read, "coverage_status", None),
                "coverage": [asdict(item) if is_dataclass(item) else item for item in getattr(read, "coverage", [])],
                "current_catalog_evidence": getattr(read, "current_catalog_evidence", None),
                "presence": "present" if observation is not None else "unknown",
                "units": getattr(read, "units", {}),
                "per_month_presence": [
                    {"data_month": item.data_month, "presence": item.presence}
                    for item in getattr(read, "months", [])
                ],
            })
            if observation is not None:
                evidence.update({
                    "source_contract": observation.source_contract,
                    "source_url": observation.source_url,
                    "source_received_at_utc": observation.received_at_utc,
                    "acquired_at": observation.acquisition_date,
                    "report_date": observation.report_date,
                    "capture_id": observation.capture_id,
                    "revision": observation.revision,
                    "payload_sha256": observation.payload_sha256,
                    "request_scope": observation.request_scope,
                    "content_hash": observation.content_hash,
                    "source": observation.source,
                })
                month = observation.data_month
                coverage_row = next(
                    (item for item in getattr(read, "coverage", []) if item.data_month == month),
                    None,
                )
                if coverage_row is not None:
                    evidence["selected_month_coverage"] = asdict(coverage_row)
                    evidence["presence"] = "present" if coverage_row.selected_issuer_present else "absent"
        else:
            evidence.update({
                "endpoint": getattr(read, "endpoint", None),
                "read_status": getattr(read, "status", None),
                "read_reason": getattr(read, "reason", None),
                "schema_ready": getattr(read, "schema_ready", None),
                "source_contract": getattr(read, "source_contract", None),
                "source_scope": getattr(read, "request_scope", None),
                "units": {"total_institutional_net_shares": getattr(read, "native_unit", None)},
                "coverage": [asdict(item) if is_dataclass(item) else item for item in getattr(read, "coverage", [])],
                "presence": "present" if observation is not None else "unknown",
            })
            if observation is not None:
                evidence.update({
                    "source_contract": observation.source_contract,
                    "source_url": observation.source_url,
                    "source_received_at_utc": observation.received_at_utc,
                    "acquired_at": observation.acquired_at,
                    "first_observed_at": observation.first_observed_at,
                    "capture_id": observation.capture_id,
                    "revision": observation.revision,
                    "payload_sha256": observation.payload_sha256,
                    "request_scope": observation.request_scope,
                })
                coverage = next(
                    (item for item in getattr(read, "coverage", []) if item.trade_date == observation.trade_date),
                    None,
                )
                if coverage is not None:
                    evidence["selected_date_coverage"] = asdict(coverage) if is_dataclass(coverage) else vars(coverage)
        return evidence

    @staticmethod
    def _condition_descriptions(conditions: dict[str, object]) -> dict[str, dict[str, str]]:
        return {
            name: {
                **_CONDITION_META[name],
                "operator": "<=" if name in {"pe_max", "pb_max"} else ">=",
                "threshold": str(value),
            }
            for name, value in conditions.items()
        }

    @staticmethod
    def _scope(pool, examined: int, selectors: dict[str, str], partial_reasons: list[str]):
        return {
            "universe": "active TWSE/TPEX EQUITY plus supported ETF from the canonical instrument catalog",
            "eligible_catalog_count": pool.eligible_catalog_count,
            "catalog_count": pool.catalog_count,
            "excluded_security_type_counts": dict(pool.excluded_security_type_counts),
            "price_scan_order": "canonical instrument_id ascending before price ranking",
            "price_universe_cap": _PRICE_UNIVERSE_CAP,
            "price_universe_selected_count": pool.price_universe_selected_count,
            "price_universe_scanned": pool.scanned_instrument_count,
            "price_universe_ids_requested": pool.scanned_instrument_count,
            "price_snapshot_rows": pool.price_snapshot_count,
            "price_snapshot_rows_returned": pool.price_snapshot_count,
            "price_snapshot_batches_planned": pool.price_snapshot_batches_planned,
            "price_snapshot_batches_attempted": pool.price_snapshot_request_count,
            "price_snapshot_batches_unattempted": pool.unattempted_price_snapshot_batches,
            "price_snapshot_batches_pending": pool.price_snapshot_batches_pending_count,
            "price_pool_candidates": pool.ranked_price_count,
            "candidate_basis": "current official EOD price snapshots ranked by turnover descending; canonical ID breaks ties",
            "candidate_limit": _MAX_CANDIDATES,
            "limits": TaiwanDiscoveryService._limits(),
            "candidates_selected": examined,
            "partial_scan": bool(partial_reasons),
            "partial_reasons": partial_reasons,
            "selector_dates": dict(selectors),
            "freshness_rules": {
                "valuation_and_institutional_flows": f"latest available source period must be within {_MAX_DAILY_AGE_DAYS} calendar days; no exchange-calendar inference",
                "monthly_revenue": f"latest present source month must be within {_MAX_REVENUE_AGE_DAYS} calendar days of month-end",
            },
            "institutional_flow_scope": "one latest available day only; consecutive-day filters are not enabled",
        }

    @staticmethod
    def _request_counts(counts: dict[str, int]) -> dict[str, int]:
        per_endpoint = {
            key: counts[key] for key in (
                "catalog", "price_snapshots", "valuation", "monthly_revenue", "institutional_flows",
            )
        }
        factor_http = {
            key.removesuffix("_http"): counts[key]
            for key in ("valuation_http", "monthly_revenue_http", "institutional_flows_http")
        }
        http_attempts = {
            "catalog": counts["catalog"],
            "price_snapshots": counts["price_snapshots"],
            **factor_http,
        }
        return {
            **per_endpoint,
            "total": sum(per_endpoint.values()),
            "http_attempts": http_attempts,
            "total_http_attempts": sum(http_attempts.values()),
            "cache_hits": counts["cache_hits"],
            "failed": counts["failed_requests"],
        }

    @staticmethod
    def _limits():
        return {
            "concurrent_screens": 2,
            "concurrent_price_reads": 4,
            "concurrent_factor_reads": 4,
            "aggregate_timeout_seconds": _REQUEST_DEADLINE_SECONDS,
            "price_timeout_seconds": 15,
            "cache_ttl_seconds": _CACHE_TTL_SECONDS,
            "cache_entries": _CACHE_LIMIT,
        }

    def _empty_result(self, conditions, selectors, today, limit, reason, source_errors, counts, pool=None, *, evaluated_at_utc):
        incomplete = reason not in {
            "quote_source_disabled", "catalog_unavailable_or_empty", "no_current_eligible_prices",
        } or bool(pool and pool.partial_scan)
        scope = {
            "universe": "active TWSE/TPEX EQUITY plus supported ETF from the canonical instrument catalog",
            "candidate_limit": _MAX_CANDIDATES,
            "limits": self._limits(),
            "candidates_selected": 0,
            "candidates_examined": 0,
            "matched_count": 0,
            "excluded_count": 0,
            "partial_scan": incomplete,
            "partial_reasons": [reason] if incomplete else [],
            "request_counts": self._request_counts(counts),
            "source_status": source_errors,
            "scan_status": pool.status if pool else reason,
            "evaluated_at_utc": evaluated_at_utc.isoformat().replace("+00:00", "Z"),
        }
        if pool is not None:
            scope.update(self._scope(pool, 0, selectors, [reason] if incomplete else []))
            scope["request_counts"] = self._request_counts(counts)
            scope["source_status"] = source_errors
        return {
            "market": "TW", "provider": "twmd_official", "selectors": selectors,
            "conditions": self._condition_descriptions(conditions), "scope": scope,
            "matches": [], "excluded": [],
        }

    @staticmethod
    def _unread_candidate(
        instrument_id: str,
        reason: str,
        security_type: str = "unknown",
        conditions: dict[str, object] | None = None,
    ):
        venue, symbol = instrument_id.split(":", 1)
        condition_results = {}
        data_evidence = {}
        for name, threshold in (conditions or {}).items():
            meta = _CONDITION_META[name]
            condition_results[name] = {
                "condition": name,
                "label": meta["label"],
                "operator": "<=" if name in {"pe_max", "pb_max"} else ">=",
                "threshold": str(threshold),
                "unit": meta["unit"],
                "value": None,
                "data_date": None,
                "passed": False,
                "reason": reason,
                "explanation": f"{meta['label']}無法判定：{reason}。",
            }
            data_evidence[meta["dataset"]] = {
                "source_contract": None,
                "source_url": None,
                "source_received_at_utc": None,
                "acquired_at": None,
                "first_observed_at": None,
                "publication_time": None,
                "report_date": None,
                "capture_id": None,
                "revision": None,
                "units": {},
                "coverage": None,
                "presence": "unknown",
                "payload_sha256": None,
                "error": reason,
            }
        return {
            "instrument_id": instrument_id, "symbol": symbol, "venue": venue,
            "market": "TW", "security_type": security_type, "name": instrument_id,
            "matched": False, "price": {}, "values": {},
            "data_dates": {"valuation": None, "monthly_revenue": None, "institutional_flows": None},
            "condition_results": condition_results,
            "data_evidence": data_evidence,
            "explanations": [item["explanation"] for item in condition_results.values()],
            "exclusion_reasons": [reason],
        }
