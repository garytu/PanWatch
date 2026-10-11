"""RC-D fixed-clock outcomes, provenance, retries, and immutable persistence checks."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.modules.strategy.outcome_kernel import (
    RETRY_BACKOFF_HOURS,
    evaluate_execution_stock,
    evaluate_forward_stock,
    retry_delay,
    sha256_json,
)
from src.platform.persistence.models import (
    Base,
    InstrumentCostAuthority,
    OutcomeEvaluationAttempt,
    RankingSnapshot,
    RankingSnapshotItem,
    StrategyOutcome,
    StrategyOutcomeV2,
)


NOW = datetime(2026, 1, 15, 11, 0, tzinfo=timezone.utc)
SESSIONS = ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-12"]


def _bar(day: str, *, open_: float = 100, high: float = 102, low: float = 99,
         close: float = 101, receipt: str | None = None, final=None,
         available: str | None = None, provider: str = "fixture",
         adjustment_mode: str = "raw") -> dict:
    value = {
        "date": day, "open": float(open_), "high": float(high), "low": float(low),
        "close": float(close), "volume": 1000.0, "provider": provider,
        "source_contract": "fixture-daily-bars/v1", "adjustment_mode": adjustment_mode,
        "volume_unit": "shares", "timeframe": "day",
    }
    content = {key: value[key] for key in (
        "date", "open", "high", "low", "close", "volume", "provider",
        "adjustment_mode", "volume_unit", "timeframe",
    )}
    return {
        **value,
        "content_sha256": sha256_json(content),
        "capture_id": f"fixture:{day}:{provider}",
        "receipt_at_utc": receipt or f"{day}T06:00:00+00:00",
        "source_available_at_utc": available,
        "final": final,
    }


def _kernel_barset():
    return [
        _bar(SESSIONS[0], open_=100, high=102, low=99, close=101),
        _bar(SESSIONS[1], open_=101, high=103, low=100, close=102),
        _bar(SESSIONS[2], open_=102, high=104, low=101, close=103),
        _bar(SESSIONS[3], open_=103, high=105, low=102, close=104),
        _bar(SESSIONS[4], open_=104, high=106, low=103, close=105),
    ]


def test_forward_requires_exact_final_daily_sessions_and_valid_source_clock():
    """Missing finalized dates stay insufficient; intraday, stale, forged, and future data fail."""
    bars = _kernel_barset()
    args = dict(expected_session_dates=SESSIONS, horizon_sessions=1, today=date(2026, 1, 15),
                now=NOW, market_timezone="Asia/Taipei", session_close="13:30")
    result = evaluate_forward_stock(bars=bars, **args)
    assert result["evaluation_status"] == "COMPLETE"
    assert result["entry_price"] == 101
    assert result["exit_price"] == 102

    missing = evaluate_forward_stock(bars=bars[:1], **args)
    assert (missing["evaluation_status"], missing["reason_code"]) == (
        "INSUFFICIENT_DATA", "required_session_missing")

    not_final = [dict(bars[0], final=False), bars[1]]
    unfinished = evaluate_forward_stock(bars=not_final, **args)
    assert unfinished["evaluation_status"] == "INSUFFICIENT_DATA"
    assert unfinished["reason_code"] == "daily_bar_unfinished"

    source_before_close = [
        _bar(SESSIONS[0], available="2026-01-06T04:00:00+00:00"), bars[1],
    ]
    assert evaluate_forward_stock(bars=source_before_close, **args)["reason_code"] == "daily_bar_unfinished"

    intraday = [dict(bars[0], date=SESSIONS[0] + "T10:30:00"), bars[1]]
    assert evaluate_forward_stock(bars=intraday, **args)["reason_code"] == "required_session_missing"

    bad_hash = [dict(bars[0], close=102), bars[1]]
    assert evaluate_forward_stock(bars=bad_hash, **args)["reason_code"] == "bar_content_identity_mismatch"

    mixed = [bars[0], _bar(SESSIONS[1], provider="other")]
    assert evaluate_forward_stock(bars=mixed, **args)["reason_code"] == "stock_window_invalid"

    duplicate_outside = [*bars[:2], _bar("2026-02-02"), _bar("2026-02-02")]
    assert evaluate_forward_stock(bars=duplicate_outside, **args)["evaluation_status"] == "COMPLETE"

    future_receipt = [dict(bars[0], receipt_at_utc="2026-01-16T06:00:00+00:00"), bars[1]]
    assert evaluate_forward_stock(bars=future_receipt, **args)["reason_code"] == "bar_receipt_from_future"

    future_source = [dict(bars[0], source_available_at_utc="2026-01-16T06:00:00+00:00"), bars[1]]
    assert evaluate_forward_stock(bars=future_source, **args)["reason_code"] == "bar_source_time_from_future"

    adjusted = [_bar(SESSIONS[0], adjustment_mode="qfq"), bars[1]]
    assert evaluate_forward_stock(bars=adjusted, **args)["reason_code"] == "stock_adjustment_basis_unsupported"

    before_close = evaluate_forward_stock(
        bars=bars, **{**args, "now": datetime(2026, 1, 6, 4, 0, tzinfo=timezone.utc)}
    )
    assert before_close["evaluation_status"] == "PENDING"


def test_execution_waits_for_expiry_and_applies_open_gap_and_same_bar_rules():
    """The simulation uses a 3-session limit window, gap-first exits, and h-th close expiry."""
    bars = _kernel_barset()
    base = dict(expected_session_dates=SESSIONS, horizon_sessions=2,
                today=date(2026, 1, 15), now=NOW,
                market_timezone="Asia/Taipei", session_close="13:30",
                action="buy", is_holding_snapshot=False, entry_low=99,
                entry_high=100, stop_loss=90, target_price=120)
    no_touch = [
        _bar(SESSIONS[0], open_=105, high=106, low=101, close=104),
        _bar(SESSIONS[1], open_=104, high=105, low=101, close=103),
        _bar(SESSIONS[2], open_=103, high=104, low=100, close=102),
        _bar(SESSIONS[3], open_=101, high=103, low=100, close=102),
        _bar(SESSIONS[4], open_=102, high=103, low=101, close=102.5),
    ]
    expiry = evaluate_execution_stock(bars=no_touch, **base)
    assert expiry["evaluation_status"] == "COMPLETE"
    assert expiry["entry_session_date"] == SESSIONS[2]
    assert expiry["exit_session_date"] == SESSIONS[4]
    assert expiry["exit_price"] == 102.5

    gap = [
        _bar(SESSIONS[0], open_=100, high=105, low=98, close=103),
        _bar(SESSIONS[1], open_=85, high=90, low=80, close=87),
        *_kernel_barset()[2:],
    ]
    gap_result = evaluate_execution_stock(bars=gap, **{**base, "horizon_sessions": 1})
    assert gap_result["exit_price"] == 85
    assert gap_result["barrier_order"] == "STOP_FIRST"

    ambiguous = [
        _bar(SESSIONS[0], open_=105, high=106, low=101, close=104),
        _bar(SESSIONS[1], open_=104, high=105, low=101, close=103),
        _bar(SESSIONS[2], open_=103, high=104, low=100, close=102),
        _bar(SESSIONS[3], open_=101, high=123, low=89, close=101),
        _bar(SESSIONS[4]),
    ]
    same_bar = evaluate_execution_stock(bars=ambiguous, **{**base, "horizon_sessions": 1})
    assert same_bar["evaluation_status"] == "AMBIGUOUS"
    assert same_bar["barrier_order"] == "BOTH_SAME_BAR"

    invalid_open = [
        _bar(SESSIONS[0], open_=85, high=90, low=80, close=87),
        *_kernel_barset()[1:],
    ]
    invalidated = evaluate_execution_stock(bars=invalid_open, **{**base, "horizon_sessions": 1})
    assert invalidated["entry_status"] == "NOT_FILLED"
    assert invalidated["reason_code"] == "entry_invalidated_at_open"

    assert evaluate_execution_stock(
        bars=bars, **{**base, "horizon_sessions": 1, "action": "buy", "is_holding_snapshot": True}
    )["reason_code"] == "order_not_applicable"
    assert evaluate_execution_stock(
        bars=bars, **{**base, "horizon_sessions": 1, "action": "add", "is_holding_snapshot": False}
    )["reason_code"] == "order_not_applicable"


def test_retry_schedule_is_bounded_at_twenty_four_hours():
    """The source retry cadence uses 1h, 6h, then a bounded 24h interval."""
    assert RETRY_BACKOFF_HOURS == (1, 6, 24)
    assert [retry_delay(i).total_seconds() // 3600 for i in range(1, 6)] == [1, 6, 24, 24, 24]


def test_kline_capture_preserves_source_final_marker_and_timeframe():
    from src.platform.marketdata.collectors.kline_collector import _captured_kline

    captured = _captured_kline(SimpleNamespace(
        date="2026-01-06T10:30:00", open=100, high=101, low=99, close=100,
        volume=10, provider="fixture", adjustment_mode="raw", volume_unit="shares",
        timeframe="1h", final=False,
    ), source_contract="fixture-intraday/v1")
    assert captured.final is False
    assert captured.timeframe == "1h"
    assert captured.date == "2026-01-06T10:30:00"


def test_runtime_samples_implicit_clock_after_market_data_receipt(monkeypatch):
    """A collector receipt created during acquisition is not rejected as future data."""
    from datetime import timedelta
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    calls = []

    def advancing_clock():
        calls.append(len(calls))
        return NOW + timedelta(seconds=(len(calls) - 1) * 2)

    monkeypatch.setattr(outcomes, "utc_now", advancing_clock)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "known", "timezone": "Asia/Taipei", "source": "fixture calendar",
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "known", "calendar_version": "fixture-calendar-v1",
        "source": "fixture calendar", "timezone": "Asia/Taipei", "confidence": "OFFICIAL",
    })
    monkeypatch.setattr(outcomes, "is_trading_day", lambda market, day: day.isoformat() in SESSIONS)

    class CapturingCollector:
        def __init__(self, market):
            pass

        def get_klines(self, symbol, days=60):
            return [dict(_bar(day), receipt_at_utc=(NOW + timedelta(seconds=1)).isoformat())
                    for day in SESSIONS]

    class NoBenchmark:
        @staticmethod
        def benchmark_bars(*args, **kwargs):
            raise RuntimeError("offline fixture")

    monkeypatch.setattr(outcomes, "KlineCollector", CapturingCollector)
    monkeypatch.setattr(outcomes, "get_market_data", lambda: NoBenchmark())
    with Session() as db:
        capture = RankingSnapshot(capture_id="implicit-clock", stock_market="TW",
                                  session_date=SESSIONS[0], item_count=1)
        db.add(capture)
        db.flush()
        db.add(RankingSnapshotItem(
            decision_snapshot_id="implicit-clock-decision", ranking_snapshot_id=capture.id,
            stock_market="TW", stock_symbol="2330.TW", instrument_id="TWSE:2330",
            strategy_code="candidate_forward", session_date=SESSIONS[0],
            outcome_population_id="candidate-forward-v2",
            evaluation_version="candidate-session-evaluator-v2.1",
            score_version="candidate-score-v1", capture_hash="c" * 64,
        ))
        db.commit()

    result = outcomes.evaluate_point_in_time_outcomes(horizons=(1,), snapshot_days=180, limit=1)
    assert result["committed"] is True
    with Session() as db:
        forward = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="implicit-clock-decision",
            outcome_population_id="candidate-forward-v2",
        ).one()
        assert forward.evaluation_status == "COMPLETE"
        assert forward.reason_code == ""
        assert forward.stock_attempt_count == 1
    engine.dispose()


def test_candidate_public_stats_expose_legacy_basis_and_v2_partitions(monkeypatch):
    from src.modules.strategy import entry_candidates

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(entry_candidates, "SessionLocal", Session)
    stats = entry_candidates.get_entry_candidate_stats(days=30)
    assert stats["legacy_outcome_basis"] == {
        "population": "legacy-unlabelled",
        "return_basis": "legacy calendar-day close projection",
    }
    assert stats["outcomes_v2"]["basis"].startswith("versioned point-in-time outcomes")
    assert stats["outcomes_v2"]["partitions"] == []
    engine.dispose()


def test_execution_preflight_settles_nonorders_and_invalid_plans_without_calendar(monkeypatch):
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "unknown", "timezone": "Asia/Taipei", "source": None,
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "unknown", "calendar_version": "", "source": "",
        "timezone": "Asia/Taipei", "confidence": "UNKNOWN",
    })

    class NoBars:
        def __init__(self, market):
            raise AssertionError("preflight must not fetch stock bars")

    monkeypatch.setattr(outcomes, "KlineCollector", NoBars)
    with Session() as db:
        capture = RankingSnapshot(capture_id="preflight", stock_market="TW",
                                  session_date=SESSIONS[0], item_count=2)
        db.add(capture)
        db.flush()
        for decision_id, symbol, action, low, high, stop, target in (
            ("preflight-watch", "2330.TW", "watch", None, None, None, None),
            ("preflight-bad-plan", "2317.TW", "buy", None, None, None, None),
        ):
            db.add(RankingSnapshotItem(
                decision_snapshot_id=decision_id, ranking_snapshot_id=capture.id,
                stock_market="TW", stock_symbol=symbol, instrument_id=f"TWSE:{symbol[:4]}",
                strategy_code="candidate_forward", session_date=SESSIONS[0],
                outcome_population_id="candidate-forward-v2",
                evaluation_version="candidate-session-evaluator-v2.1",
                score_version="candidate-score-v1", capture_hash=("d" if action == "buy" else "e") * 64,
                action=action, is_holding_snapshot=False, entry_low=low, entry_high=high,
                stop_loss=stop, target_price=target,
            ))
        db.commit()

    result = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=2, now=NOW,
    )
    assert result["committed"] is True
    with Session() as db:
        watch = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="preflight-watch", outcome_population_id="strategy-execution-v2",
        ).one()
        invalid = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="preflight-bad-plan", outcome_population_id="strategy-execution-v2",
        ).one()
        assert (watch.evaluation_status, watch.entry_status, watch.reason_code) == (
            "COMPLETE", "NOT_FILLED", "order_not_applicable")
        assert (invalid.evaluation_status, invalid.entry_status, invalid.reason_code) == (
            "INSUFFICIENT_DATA", "INSUFFICIENT_DATA", "entry_window_invalid")
        assert watch.stock_retryable is False
        assert invalid.stock_retryable is False
    engine.dispose()


def test_execution_keeps_fill_facts_when_exit_session_is_still_open():
    bars = [
        _bar(SESSIONS[0], open_=105, high=106, low=101, close=104),
        _bar(SESSIONS[1], open_=104, high=105, low=101, close=103),
        _bar(SESSIONS[2], open_=103, high=104, low=100, close=102),
    ]
    result = evaluate_execution_stock(
        bars=bars,
        expected_session_dates=SESSIONS,
        horizon_sessions=1,
        today=date(2026, 1, 8),
        now=datetime(2026, 1, 8, 6, 0, tzinfo=timezone.utc),
        market_timezone="Asia/Taipei", session_close="13:30",
        action="buy", is_holding_snapshot=False, entry_low=99, entry_high=100,
        stop_loss=90, target_price=120,
    )
    assert result["evaluation_status"] == "PENDING"
    assert result["entry_status"] == "FILLED"
    assert result["entry_session_date"] == SESSIONS[2]
    assert result["entry_price"] == 100


def test_legacy_backtest_and_v2_policies_keep_distinct_entry_and_barrier_semantics():
    """Legacy next-open/stop-first and calendar policy labels stay separate from v2 rules."""
    from src.modules.strategy.backtest import LEGACY_OUTCOME_POLICY_METADATA
    from src.modules.strategy.backtest.data_adapter import PriceBar
    from src.modules.strategy.backtest.engine import Backtester, Signal

    source_bars = [
        _bar("2026-01-05", open_=100, high=101, low=99, close=100),
        _bar("2026-01-06", open_=100, high=105, low=99, close=101),
        _bar("2026-01-07", open_=100, high=111, low=89, close=102),
        _bar("2026-01-08", open_=102, high=104, low=101, close=103),
        _bar("2026-01-09", open_=103, high=105, low=102, close=104),
    ]
    legacy_bars = [PriceBar(date=bar["date"], open=bar["open"], high=bar["high"],
                            low=bar["low"], close=bar["close"], volume=bar["volume"])
                   for bar in source_bars]
    signal = Signal("X", "CN", "2026-01-05", stop_loss=90, target_price=110, holding_days=1)
    legacy = Backtester().run_single(signal, legacy_bars)
    assert legacy.entry_price == 100
    assert legacy.exit_price == 90
    assert legacy.exit_reason == "stop_loss"

    now = datetime(2026, 1, 12, 11, tzinfo=timezone.utc)
    forward = evaluate_forward_stock(
        bars=source_bars[1:3], expected_session_dates=["2026-01-06", "2026-01-07"],
        horizon_sessions=1, today=date(2026, 1, 12), now=now,
        market_timezone="Asia/Taipei", session_close="13:30",
    )
    assert forward["entry_price"] == 101
    assert forward["exit_price"] == 102
    assert forward["evaluation_status"] == "COMPLETE"

    execution = evaluate_execution_stock(
        bars=source_bars[1:], expected_session_dates=[bar["date"] for bar in source_bars[1:]],
        horizon_sessions=1, today=date(2026, 1, 12), now=now,
        market_timezone="Asia/Taipei", session_close="13:30",
        action="buy", is_holding_snapshot=False, entry_low=99, entry_high=100,
        stop_loss=90, target_price=110,
    )
    assert execution["evaluation_status"] == "AMBIGUOUS"
    assert execution["barrier_order"] == "BOTH_SAME_BAR"

    target_gap_bars = [source_bars[0], source_bars[1],
                       _bar("2026-01-07", open_=111, high=112, low=89, close=111),
                       *source_bars[3:]]
    legacy_gap_bars = [PriceBar(date=bar["date"], open=bar["open"], high=bar["high"],
                                low=bar["low"], close=bar["close"], volume=bar["volume"])
                       for bar in target_gap_bars]
    assert Backtester().run_single(signal, legacy_gap_bars).exit_price == 90
    v2_gap = evaluate_execution_stock(
        bars=target_gap_bars[1:],
        expected_session_dates=[bar["date"] for bar in target_gap_bars[1:]],
        horizon_sessions=1, today=date(2026, 1, 12), now=now,
        market_timezone="Asia/Taipei", session_close="13:30",
        action="buy", is_holding_snapshot=False, entry_low=99, entry_high=100,
        stop_loss=90, target_price=110,
    )
    assert v2_gap["exit_price"] == 111
    assert v2_gap["barrier_order"] == "TARGET_FIRST"

    exported = Backtester().run([signal], {("X", "CN"): legacy_bars})
    assert exported.metadata["outcome_policies"] == LEGACY_OUTCOME_POLICY_METADATA
    assert "stop-first" in exported.metadata["outcome_policies"]["backtest_execution_v1"]["same_bar_policy"]
    assert exported.metadata["outcome_policies"]["calendar_horizon_return_v1"]["horizon_basis"] == "calendar days"


def test_v2_runtime_commits_separate_populations_and_immutable_attempts(monkeypatch):
    """Runtime capture stores bars, keeps populations distinct, and isolates benchmark retries."""
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    open_days = set(SESSIONS)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "known", "timezone": "Asia/Taipei", "source": "fixture calendar",
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "known", "calendar_version": "fixture-calendar-v1",
        "source": "fixture calendar", "timezone": "Asia/Taipei", "confidence": "OFFICIAL",
    })
    monkeypatch.setattr(outcomes, "is_trading_day", lambda market, day: day.isoformat() in open_days)
    fixture_bars = [_bar(day) for day in SESSIONS]

    class FakeCollector:
        reads = 0

        def __init__(self, market):
            self.market = market

        def get_klines(self, symbol, days=60):
            type(self).reads += 1
            return fixture_bars

    class FakeBenchmarkData:
        available = False
        reads = 0
        mode = "valid"

        @classmethod
        def benchmark_bars(cls, benchmark_id, *, start_date, end_date, limit, timeout_sec):
            cls.reads += 1
            if not cls.available:
                raise RuntimeError("test benchmark unavailable")
            from marketdata.types import (
                TwmdBenchmarkBar, TwmdBenchmarkCoverageDay, TwmdBenchmarkProvenance,
            )
            source_url = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
            contract = "TWSE fixture daily OHLC"
            bars = []
            provenance = []
            current = date.fromisoformat(start_date)
            last = date.fromisoformat(end_date)
            while current <= last:
                if current.weekday() < 5:
                    day = current.isoformat()
                    capture_id = f"fixture-index-{day}"
                    digest = (day.replace("-", "") + "0" * 58)[:64]
                    bars.append(TwmdBenchmarkBar(
                        benchmark_id=benchmark_id, trade_date=day,
                        open=Decimal("100"), high=Decimal("102"), low=Decimal("99"),
                        close=Decimal("101" if day == start_date else "102"),
                        unit="index_points", basis="raw_price_index", revision=1,
                        capture_id=capture_id, captured_at="2026-01-15T10:00:00Z",
                        source_contract=contract, source_alias="MI_5MINS_HIST",
                        source_url=source_url, request_scope=day[:7], payload_sha256=digest,
                    ))
                    provenance.append(TwmdBenchmarkProvenance(
                        capture_id=capture_id, benchmark_id=benchmark_id,
                        captured_at="2026-01-15T10:00:00Z", acquisition_date="2026-01-15",
                        source_contract=contract, source_alias="MI_5MINS_HIST",
                        source_url=source_url, request_scope=day[:7],
                        publication_start=day, publication_end=day,
                        payload_sha256=digest, record_count=1,
                    ))
                current += __import__("datetime").timedelta(days=1)
            coverage = []
            current = date.fromisoformat(start_date)
            while current <= last:
                day = current.isoformat()
                coverage.append(TwmdBenchmarkCoverageDay(
                    trade_date=day,
                    status="AVAILABLE" if any(bar.trade_date == day for bar in bars) else "MISSING",
                ))
                current += __import__("datetime").timedelta(days=1)
            gaps = [item.trade_date for item in coverage if item.status == "MISSING"]
            result = SimpleNamespace(
                benchmark_id=benchmark_id, venue="TWSE", provider="TWSE",
                source_alias="MI_5MINS_HIST", source_contract=contract, source_url=source_url,
                timeframe="day", price_kind="benchmark_index", adjustment_mode="raw_price_index",
                unit="index_points", basis="raw_price_index", limit=limit,
                returned_count=len(bars), total_count=len(bars), partial=bool(gaps),
                truncated=False, requested_start_date=start_date, requested_end_date=end_date,
                coverage_window={"start_date": start_date, "end_date": end_date,
                                 "complete": not gaps, "schema_ready": True,
                                 "evidence_truncated": False, "available_count": len(bars),
                                 "missing_count": len(gaps)},
                coverage=tuple(coverage), gaps=tuple(gaps), bars=tuple(bars),
                provenance=tuple(provenance), provenance_total_count=len(provenance),
                provenance_truncated=False, served_at="2026-01-15T11:00:00Z",
            )
            from dataclasses import replace
            if cls.mode == "future_served":
                result.served_at = "2026-01-16T11:00:00Z"
            elif cls.mode in {"future_capture", "before_close"}:
                captured = "2026-01-16T10:00:00Z" if cls.mode == "future_capture" else "2026-01-06T04:00:00Z"
                result.bars = tuple(replace(bar, captured_at=captured) for bar in result.bars)
                result.provenance = tuple(replace(capture, captured_at=captured) for capture in result.provenance)
            elif cls.mode == "float_overflow":
                result.bars = tuple(replace(bar, open=Decimal("1e999"), high=Decimal("1e999"),
                                            low=Decimal("1e999"), close=Decimal("1e999")) for bar in result.bars)
            return result

    monkeypatch.setattr(outcomes, "KlineCollector", FakeCollector)
    monkeypatch.setattr(outcomes, "get_market_data", lambda: FakeBenchmarkData())
    with Session() as db:
        capture = RankingSnapshot(
            capture_id="rcd-fixture", stock_market="TW", session_date="2026-01-05",
            ranker_version="ranker-v1", item_count=1,
        )
        db.add(capture)
        db.flush()
        db.add(RankingSnapshotItem(
            decision_snapshot_id="decision-fixture-1", ranking_snapshot_id=capture.id,
            stock_market="TW", stock_symbol="2330.TW", instrument_id="TWSE:2330",
            strategy_code="candidate_forward", session_date="2026-01-05",
            outcome_population_id="candidate-forward-v2",
            evaluation_version="candidate-session-evaluator-v2.1",
            ranker_version="candidate-score-v1", score_version="candidate-score-v1",
            ranking_value=80.0, capture_hash="f" * 64,
            action="buy", is_holding_snapshot=False, entry_low=99.0, entry_high=100.0,
            stop_loss=90.0, target_price=120.0, candidate_source="market_scan",
            instrument_group="EQUITY", instrument_authority={"venue": "TWSE"},
        ))
        db.commit()

    result = outcomes.evaluate_point_in_time_outcomes(horizons=(1,), snapshot_days=45,
                                                       limit=10, now=NOW)
    assert "error" not in result, result
    assert result["attempted"] == 2
    with Session() as db:
        rows = db.query(StrategyOutcomeV2).order_by(StrategyOutcomeV2.outcome_population_id).all()
        assert {row.outcome_population_id for row in rows} == {
            "candidate-forward-v2", "strategy-execution-v2",
        }
        assert {row.evaluation_status for row in rows} == {"COMPLETE"}
        assert all(row.stock_facts_hash and row.stock_input_hash for row in rows)
        assert all(row.decision_score == 80.0 and row.decision_capture_hash == "f" * 64 for row in rows)
        attempts = db.query(OutcomeEvaluationAttempt).all()
        stock_attempts = [x for x in attempts if x.attempt_kind == "stock"]
        benchmark_attempts = [x for x in attempts if x.attempt_kind == "benchmark"]
        assert len(stock_attempts) == len(benchmark_attempts) == 2
        assert all(x.input_snapshot["stock_bars"] for x in stock_attempts)
        assert all(row.benchmark_status == "PENDING" for row in rows)
        hashes = {row.id: (row.stock_input_hash, row.stock_facts_hash) for row in rows}
        stock_collector_reads = FakeCollector.reads
        benchmark_reads_before_retry = FakeBenchmarkData.reads
        FakeBenchmarkData.available = True
        retried = outcomes.evaluate_point_in_time_outcomes(
            horizons=(1,), snapshot_days=45, limit=10,
            now=NOW + __import__("datetime").timedelta(hours=2),
        )
        assert retried["committed"] is True
        assert FakeCollector.reads == stock_collector_reads
        assert FakeBenchmarkData.reads == benchmark_reads_before_retry + 1
        db.expire_all()
        assert {row.id: (row.stock_input_hash, row.stock_facts_hash) for row in rows} == hashes
        assert all(row.benchmark_status == "AVAILABLE" for row in rows), [
            (row.benchmark_status, row.benchmark_reason_code,
             [(x.input_snapshot or {}).get("error_type") for x in db.query(OutcomeEvaluationAttempt)
              .filter_by(attempt_kind="benchmark").all()],
             [x.reason_code for x in db.query(OutcomeEvaluationAttempt)
              .filter_by(attempt_kind="benchmark").all()]) for row in rows
        ]
        assert {row.benchmark_id for row in rows} == {"TAIEX"}
        for mode, expected_reason in (
            ("future_served", "benchmark_served_at_from_future"),
            ("future_capture", "benchmark_provenance_from_future"),
            ("before_close", "benchmark_bar_not_finalized"),
            ("float_overflow", "benchmark_bar_ohlc_invalid"),
        ):
            FakeBenchmarkData.mode = mode
            _, status, reason, _ = outcomes._benchmark_attempt_payload(rows[0], NOW)
            assert (status, reason) == ("UNAVAILABLE", expected_reason)
        FakeBenchmarkData.mode = "valid"
        for row in rows:
            row.benchmark_reason_code = "still-unavailable"
        db.commit()
        assert {row.id: (row.stock_input_hash, row.stock_facts_hash) for row in rows} == hashes
        row = rows[0]
        row.stock_symbol = "rewritten"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        attempt = stock_attempts[0]
        attempt.reason_code = "rewrite"
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        with pytest.raises(IntegrityError):
            db.delete(stock_attempts[0])
            db.commit()
        db.rollback()

    # Existing calendar-day rows and the RC-D projections remain separate tables.
    assert "strategy_outcomes" in Base.metadata.tables
    assert "strategy_outcomes_v2" in Base.metadata.tables
    assert "outcome_evaluation_attempts" in Base.metadata.tables
    engine.dispose()


def test_dated_costs_use_both_entry_and_exit_authorities(monkeypatch):
    """Net return is null without authority and uses independently dated leg policies."""
    from src.modules.strategy import backtest
    from src.modules.strategy.backtest import cost_model
    from src.modules.strategy.outcomes import _apply_costs

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(cost_model, "trading_lot", lambda market: 10)
    with Session() as db:
        for version, start, end, commission in (
            ("entry-policy", "2026-01-01", "2026-01-06", 0.001),
            ("exit-policy", "2026-01-07", None, 0.002),
        ):
            db.add(InstrumentCostAuthority(
                instrument_id="TWSE:2330", tax_category="STOCK",
                classification_authority="fixture exchange rule",
                classification_source="fixture authority", policy_version=version,
                effective_from=start, effective_to=end, stamp_duty_rate=0.003,
                commission_rate=commission, minimum_commission=0.0,
                transfer_fee_rate=0.0, slippage_bps=0.0, evidence_hash="a" * 64,
            ))
        db.commit()
        decision = {"stock_market": "TW", "instrument_id": "TWSE:2330"}
        result = {"entry_status": "FILLED", "entry_session_date": "2026-01-06",
                  "exit_session_date": "2026-01-07", "entry_price": 100.0,
                  "exit_price": 102.0, "stock_gross_return_pct": 2.0}
        from src.modules.strategy.outcome_kernel import (
            POPULATION_SIGNAL_FORWARD_V2, POPULATION_STRATEGY_EXECUTION_V2,
        )
        costs = _apply_costs(db, decision, result, population=POPULATION_SIGNAL_FORWARD_V2)
        assert costs["reason_code"] == ""
        assert costs["quantity"] is None
        assert costs["net_return_pct"] < result["stock_gross_return_pct"]
        assert costs["breakdown"]["quantity_policy"] == "not_required_fixed_notional_return"
        assert costs["breakdown"]["cost_method"] == "dated_basis_points_per_side_on_entry_and_exit"
        assert costs["breakdown"]["entry_cost_bps"] == 10
        assert costs["breakdown"]["exit_cost_bps"] == 50
        execution_costs = _apply_costs(
            db, decision, result, population=POPULATION_STRATEGY_EXECUTION_V2,
        )
        assert execution_costs["quantity"] == execution_costs["lot_size"] == 10
        round_trip = execution_costs["breakdown"]["round_trip"]
        assert round_trip["buy"]["commission"] == 1.0
        assert round_trip["sell"]["commission"] == 2.04
        assert round_trip["sell"]["stamp_duty"] == 3.06
        unsupported_execution = _apply_costs(
            db, {"stock_market": "CN", "instrument_id": "SH:600000"}, result,
            population=POPULATION_STRATEGY_EXECUTION_V2,
        )
        assert unsupported_execution["reason_code"] == "invalid_explicit_cost_policy"
        zero_net = _apply_costs(
            db, decision, {**result, "stock_gross_return_pct": 0.6},
            population=POPULATION_SIGNAL_FORWARD_V2,
        )
        negative_net = _apply_costs(
            db, decision, {**result, "stock_gross_return_pct": 0.5},
            population=POPULATION_SIGNAL_FORWARD_V2,
        )
        assert zero_net["net_return_pct"] == 0.0
        assert negative_net["net_return_pct"] == -0.1
    engine.dispose()


def test_runtime_accepts_explicit_evaluator_revision_as_a_coexisting_population(monkeypatch):
    """A manual revision creates its own immutable row and policy identity at runtime."""
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    open_days = set(SESSIONS)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "known", "timezone": "Asia/Taipei", "source": "fixture calendar",
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "known", "calendar_version": "fixture-calendar-v1",
        "source": "fixture calendar", "timezone": "Asia/Taipei", "confidence": "OFFICIAL",
    })
    monkeypatch.setattr(outcomes, "is_trading_day", lambda market, day: day.isoformat() in open_days)
    fixture_bars = [_bar(day) for day in SESSIONS]

    class FakeCollector:
        def __init__(self, market):
            self.market = market

        def get_klines(self, symbol, days=60):
            return fixture_bars

    class NoBenchmark:
        @staticmethod
        def benchmark_bars(*args, **kwargs):
            raise RuntimeError("benchmark source unavailable in fixture")

    monkeypatch.setattr(outcomes, "KlineCollector", FakeCollector)
    monkeypatch.setattr(outcomes, "get_market_data", lambda: NoBenchmark())
    with Session() as db:
        capture = RankingSnapshot(capture_id="revision-capture", stock_market="TW",
                                  session_date="2026-01-05", item_count=1)
        db.add(capture)
        db.flush()
        db.add(RankingSnapshotItem(
            decision_snapshot_id="revision-decision", ranking_snapshot_id=capture.id,
            stock_market="TW", stock_symbol="2330.TW", instrument_id="TWSE:2330",
            strategy_code="candidate_forward", session_date="2026-01-05",
            outcome_population_id="candidate-forward-v2",
            evaluation_version="candidate-session-evaluator-v2.1",
            score_version="candidate-score-v1", capture_hash="c" * 64,
            action="watch", is_holding_snapshot=False,
        ))
        db.commit()

    baseline = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10, now=NOW,
    )
    assert baseline["committed"] is True
    assert baseline["evaluation_revision"] == "baseline"
    rollout = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10, now=NOW,
        evaluation_revision="reviewed-2026-10-11",
    )
    assert rollout["committed"] is True
    assert rollout["evaluation_revision"] == "reviewed-2026-10-11"
    with Session() as db:
        rows = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="revision-decision",
        ).all()
        assert len(rows) == 4
        by_population = {}
        for row in rows:
            by_population.setdefault(row.outcome_population_id, []).append(row)
            assert row.evaluation_status == "COMPLETE"
        assert set(by_population) == {"candidate-forward-v2", "strategy-execution-v2"}
        for population_rows in by_population.values():
            assert len(population_rows) == 2
            baseline_row = next(row for row in population_rows if "@" not in row.evaluation_version)
            revision_row = next(row for row in population_rows if "@reviewed-2026-10-11" in row.evaluation_version)
            assert revision_row.policy_payload["evaluation_revision"] == "reviewed-2026-10-11"
            assert revision_row.policy_hash != baseline_row.policy_hash
            assert revision_row.stock_facts_hash != baseline_row.stock_facts_hash

    with pytest.raises(ValueError, match="evaluation_revision"):
        outcomes.evaluate_point_in_time_outcomes(
            horizons=(1,), now=NOW, evaluation_revision="unsafe/revision",
        )
    engine.dispose()


def test_runtime_queue_prioritizes_due_retry_over_newer_complete_and_not_due_rows(monkeypatch):
    """A limit-one scheduler tick services the older due retry before newer snapshots."""
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    open_days = set(SESSIONS)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "known", "timezone": "Asia/Taipei", "source": "fixture calendar",
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "known", "calendar_version": "fixture-calendar-v1",
        "source": "fixture calendar", "timezone": "Asia/Taipei", "confidence": "OFFICIAL",
    })
    monkeypatch.setattr(outcomes, "is_trading_day", lambda market, day: day.isoformat() in open_days)

    class FakeCollector:
        reads = 0

        def __init__(self, market):
            self.market = market

        def get_klines(self, symbol, days=60):
            type(self).reads += 1
            return [_bar(day) for day in SESSIONS]

    class NoBenchmark:
        @staticmethod
        def benchmark_bars(*args, **kwargs):
            raise RuntimeError("benchmark source unavailable in fixture")

    monkeypatch.setattr(outcomes, "KlineCollector", FakeCollector)
    monkeypatch.setattr(outcomes, "get_market_data", lambda: NoBenchmark())
    with Session() as db:
        capture = RankingSnapshot(capture_id="queue-capture", stock_market="TW",
                                  session_date="2026-01-13", item_count=3)
        db.add(capture)
        db.flush()
        rows = []
        for decision_id, code, day in (
            ("queue-old-due", "2330", "2026-01-05"),
            ("queue-new-complete", "2317", "2026-01-12"),
            ("queue-new-notdue", "2454", "2026-01-13"),
        ):
            item = RankingSnapshotItem(
                decision_snapshot_id=decision_id, ranking_snapshot_id=capture.id,
                stock_market="TW", stock_symbol=f"{code}.TW", instrument_id=f"TWSE:{code}",
                strategy_code="candidate_forward", session_date=day,
                outcome_population_id="candidate-forward-v2",
                evaluation_version="candidate-session-evaluator-v2.1",
                score_version="candidate-score-v1", capture_hash=(code[0] * 64),
                action="watch", is_holding_snapshot=False,
            )
            db.add(item)
            rows.append((decision_id, code, day))
        db.flush()
        for decision_id, code, day in rows:
            for population, evaluator, status, retry_at in (
                ("candidate-forward-v2", "candidate-session-evaluator-v2.1",
                 "INSUFFICIENT_DATA" if decision_id == "queue-old-due" else "COMPLETE",
                 datetime(2026, 1, 14, 0) if decision_id == "queue-old-due" else None),
                ("strategy-execution-v2", "strategy-execution-evaluator-v2.1",
                 "COMPLETE" if decision_id != "queue-new-notdue" else "PENDING",
                 datetime(2026, 1, 16, 0) if decision_id == "queue-new-notdue" else None),
            ):
                is_due_row = decision_id == "queue-old-due" and population == "candidate-forward-v2"
                is_future_row = decision_id == "queue-new-notdue"
                db.add(StrategyOutcomeV2(
                    decision_snapshot_id=decision_id, stock_symbol=f"{code}.TW", stock_market="TW",
                    instrument_id=f"TWSE:{code}", strategy_code="candidate_forward", session_date=day,
                    outcome_population_id=population, horizon_sessions=1,
                    evaluation_version=evaluator, score_version="candidate-score-v1",
                    policy_version="fixture-v1", policy_payload={}, policy_hash=(code[0] * 64),
                    decision_point_in_time_status="UNVERIFIABLE",
                    evaluation_status=status,
                    entry_status="PENDING" if is_due_row or is_future_row else "NOT_FILLED",
                    stock_retryable=bool(is_due_row or is_future_row),
                    stock_first_attempt_at=datetime(2026, 1, 5, 0),
                    stock_retry_deadline_at=datetime(2026, 2, 4, 0),
                    stock_next_retry_at=retry_at,
                    benchmark_retryable=False,
                ))
        db.commit()

    result = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=1, now=NOW,
    )
    assert result["committed"] is True
    assert result["decisions"] == 1
    assert FakeCollector.reads == 1
    with Session() as db:
        old = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="queue-old-due", outcome_population_id="candidate-forward-v2",
        ).one()
        assert old.evaluation_status == "COMPLETE"
        assert old.stock_attempt_count == 1
        newer_ids = {"queue-new-complete", "queue-new-notdue"}
        assert all(db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id=decision_id,
        ).count() == 2 for decision_id in newer_ids)
    engine.dispose()


def test_public_v2_stats_partition_and_report_row_unit_and_availability_denominators(monkeypatch):
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    monkeypatch.setattr(outcomes, "utc_now", lambda: datetime(2026, 1, 15, 12, tzinfo=timezone.utc))
    with Session() as db:
        capture = RankingSnapshot(capture_id="stats-capture", stock_market="TW",
                                  session_date="2026-01-05", item_count=5)
        db.add(capture)
        db.flush()
        values = [
            ("stats-1", "TWSE:2330", "2026-01-05", "FILLED", "COMPLETE", 2.0, 1.0, 0.5, "AVAILABLE", 1.0),
            ("stats-2", "TWSE:2330", "2026-01-05", "FILLED", "COMPLETE", 4.0, 3.0, 1.5, "PENDING", None),
            ("stats-3", "TWSE:2317", "2026-01-06", "FILLED", "COMPLETE", -1.0, -2.0, -1.0, "UNAVAILABLE", None),
            ("stats-4", "TWSE:2454", "2026-01-06", "NOT_FILLED", "COMPLETE", None, None, None, "PENDING", None),
            ("stats-5", "TWSE:2308", "2026-01-06", "INSUFFICIENT_DATA", "INSUFFICIENT_DATA", None, None, None, "PENDING", None),
        ]
        for index, (decision_id, instrument, day, entry_status, status, gross, net, relative,
                    benchmark_status, benchmark_gross) in enumerate(values):
            db.add(RankingSnapshotItem(
                decision_snapshot_id=decision_id, ranking_snapshot_id=capture.id,
                stock_market="TW", stock_symbol=instrument.split(":")[1], instrument_id=instrument,
                strategy_code="trend_follow", session_date=day,
                outcome_population_id="signal-forward-v2", evaluation_version="signal-session-evaluator-v2.1",
                score_version="ranker-v1", capture_hash=f"{index + 1:064x}",
            ))
            db.flush()
            db.add(StrategyOutcomeV2(
                decision_snapshot_id=decision_id, stock_market="TW", stock_symbol=instrument.split(":")[1],
                instrument_id=instrument, strategy_code="trend_follow", session_date=day,
                outcome_population_id="signal-forward-v2", horizon_sessions=1,
                evaluation_version="signal-session-evaluator-v2.1", score_version="ranker-v1",
                policy_version="forward-next-session-close-h-v2.1", policy_payload={},
                policy_hash=f"{index + 1:064x}", decision_point_in_time_status="UNVERIFIABLE",
                entry_status=entry_status, evaluation_status=status,
                stock_gross_return_pct=gross, stock_net_return_pct=net,
                relative_net_percentage_points=relative,
                benchmark_status=benchmark_status, benchmark_gross_return_pct=benchmark_gross,
                stock_retryable=(status == "INSUFFICIENT_DATA"), quantity=1000 if entry_status == "FILLED" else None,
            ))
        # An old decision-session date is excluded even though its row was just inserted.
        db.add(RankingSnapshotItem(
            decision_snapshot_id="stats-old", ranking_snapshot_id=capture.id,
            stock_market="TW", stock_symbol="2330", instrument_id="TWSE:2330",
            strategy_code="trend_follow", session_date="2025-09-01",
        ))
        db.flush()
        db.add(StrategyOutcomeV2(
            decision_snapshot_id="stats-old", stock_market="TW", stock_symbol="2330",
            instrument_id="TWSE:2330", strategy_code="trend_follow", session_date="2025-09-01",
            outcome_population_id="signal-forward-v2", horizon_sessions=1,
            evaluation_version="signal-session-evaluator-v2.1", score_version="ranker-v1",
            entry_status="FILLED", evaluation_status="COMPLETE", stock_gross_return_pct=99.0,
        ))
        db.commit()
        result = outcomes.point_in_time_outcome_stats(days=30, db=db)
        assert len(result["partitions"]) == 1
        stats = result["partitions"][0]
        assert stats["raw_outcome_rows"] == 5
        assert stats["decision_date_count"] == 2
        assert stats["distinct_units"] == 4
        assert stats["entry_count"] == 3
        assert stats["not_filled"] == 1
        assert stats["insufficient_data"] == 1
        assert stats["gross_row_denominator"] == 3
        assert stats["gross_denominator_units"] == 2
        assert stats["gross_mean_return_pct"] == 1.0
        assert stats["gross_win_rate_pct"] == 50.0
        assert stats["net_row_denominator"] == 3
        assert stats["net_denominator_units"] == 2
        assert stats["net_mean_return_pct"] == 0.0
        assert stats["benchmark_available_rows"] == 1
        assert stats["benchmark_pending_rows"] == 3
        assert stats["benchmark_unavailable_rows"] == 1
        assert stats["benchmark_availability_denominator_units"] == 2
        assert stats["benchmark_available_units"] == 1
        assert stats["benchmark_unavailable_units"] == 1
        assert stats["benchmark_pending_units"] == 2
    engine.dispose()


def test_runtime_retries_same_projection_then_caps_observation_at_thirty_days(monkeypatch):
    from src.modules.strategy import outcomes

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    from src.platform.persistence.migrations import run_versioned_migrations
    run_versioned_migrations(engine)
    monkeypatch.setattr(outcomes, "SessionLocal", Session)
    monkeypatch.setattr(outcomes, "market_calendar_context", lambda market, day: {
        "status": "known", "timezone": "Asia/Taipei", "source": "fixture calendar",
    })
    monkeypatch.setattr(outcomes, "market_calendar_evidence", lambda market, start, end: {
        "status": "known", "calendar_version": "fixture-calendar-v1",
        "source": "fixture calendar", "timezone": "Asia/Taipei", "confidence": "OFFICIAL",
    })
    monkeypatch.setattr(outcomes, "is_trading_day", lambda market, day: day.weekday() < 5)
    bars_by_symbol = {"2330.TW": [_bar(SESSIONS[0])], "2317.TW": []}

    class FakeCollector:
        reads = []

        def __init__(self, market):
            self.market = market

        def get_klines(self, symbol, days=60):
            type(self).reads.append(symbol)
            return bars_by_symbol[symbol]

    class NoBenchmark:
        @staticmethod
        def benchmark_bars(*args, **kwargs):
            raise RuntimeError("test benchmark unavailable")

    monkeypatch.setattr(outcomes, "KlineCollector", FakeCollector)
    monkeypatch.setattr(outcomes, "get_market_data", lambda: NoBenchmark())
    with Session() as db:
        capture = RankingSnapshot(capture_id="retry-capture", stock_market="TW",
                                  session_date="2026-01-05", item_count=2)
        db.add(capture)
        db.flush()
        for code in ("2330", "2317"):
            db.add(RankingSnapshotItem(
                decision_snapshot_id=f"retry-{code}", ranking_snapshot_id=capture.id,
                stock_market="TW", stock_symbol=f"{code}.TW", instrument_id=f"TWSE:{code}",
                strategy_code="candidate_forward", session_date="2026-01-05",
                outcome_population_id="candidate-forward-v2",
                evaluation_version="candidate-session-evaluator-v2.1",
                score_version="candidate-score-v1", capture_hash=("a" if code == "2330" else "b") * 64,
                action="watch", is_holding_snapshot=False,
            ))
        db.commit()

    first = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10,
        now=datetime(2026, 1, 6, 4, 0, tzinfo=timezone.utc),
    )
    assert first["committed"] is True
    with Session() as db:
        success = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="retry-2330", outcome_population_id="candidate-forward-v2",
        ).one()
        late = db.query(StrategyOutcomeV2).filter_by(
            decision_snapshot_id="retry-2317", outcome_population_id="candidate-forward-v2",
        ).one()
        success_id, late_id = success.id, late.id
        assert success.evaluation_status == late.evaluation_status == "PENDING"
        assert success.stock_attempt_count == late.stock_attempt_count == 1
        assert success.stock_retry_deadline_at == datetime(2026, 2, 5, 4, 0)

    bars_by_symbol["2330.TW"] = [_bar(SESSIONS[0])]
    second = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10,
        now=datetime(2026, 1, 8, 6, 0, tzinfo=timezone.utc),
    )
    assert second["attempted"] >= 2
    with Session() as db:
        success = db.get(StrategyOutcomeV2, success_id)
        late = db.get(StrategyOutcomeV2, late_id)
        assert success.evaluation_status == late.evaluation_status == "INSUFFICIENT_DATA"
        assert success.reason_code == late.reason_code == "required_session_missing"
        assert success.stock_attempt_count == late.stock_attempt_count == 2

    bars_by_symbol["2330.TW"] = [_bar(day) for day in SESSIONS[:2]]
    third = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10,
        now=datetime(2026, 1, 8, 13, 0, tzinfo=timezone.utc),
    )
    assert third["committed"] is True
    with Session() as db:
        success = db.get(StrategyOutcomeV2, success_id)
        late = db.get(StrategyOutcomeV2, late_id)
        assert success.evaluation_status == "COMPLETE", (success.reason_code, success.stock_retryable, success.stock_next_retry_at)
        assert success.stock_attempt_count == 3
        assert late.evaluation_status == "INSUFFICIENT_DATA"
        assert late.stock_retryable is True
        assert late.stock_attempt_count == 3

    # Valid final bars arriving after the frozen observation deadline cannot complete the row.
    bars_by_symbol["2317.TW"] = [_bar(day) for day in SESSIONS[:2]]
    reads_before_deadline = list(FakeCollector.reads)
    final = outcomes.evaluate_point_in_time_outcomes(
        horizons=(1,), snapshot_days=180, limit=10,
        now=datetime(2026, 2, 6, 5, 0, tzinfo=timezone.utc),
    )
    assert final["committed"] is True
    assert FakeCollector.reads == reads_before_deadline
    with Session() as db:
        late = db.get(StrategyOutcomeV2, late_id)
        assert late.evaluation_status == "INSUFFICIENT_DATA"
        assert late.stock_retryable is False
        assert late.reason_code == "retry_deadline_exceeded"
        assert late.stock_attempt_count == 4
        attempts = db.query(OutcomeEvaluationAttempt).filter(
            OutcomeEvaluationAttempt.outcome_id == late_id,
            OutcomeEvaluationAttempt.attempt_kind == "stock",
        ).order_by(OutcomeEvaluationAttempt.attempt_number).all()
        assert [item.attempt_number for item in attempts] == [1, 2, 3, 4]
    engine.dispose()
