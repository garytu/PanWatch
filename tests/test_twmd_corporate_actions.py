from __future__ import annotations

from datetime import date

from marketdata.errors import TwmdReadError
from marketdata.types import (
    TwmdCapitalReductionObservation,
    TwmdCapitalReductionRead,
    TwmdExRightDividendObservation,
    TwmdExRightDividendRead,
)
from src.modules.research import taiwan_research
from src.platform.marketdata.collectors.kline_collector import KlineData
from src.platform.marketdata.collectors.screenshot_collector import taiwan_chart_html


def _ex_read(instrument_id="TWSE:123A"):
    row = TwmdExRightDividendObservation(
        effective_date="2024-06-25", instrument_id=instrument_id, symbol=instrument_id.split(":")[1],
        observed_name="Synthetic Co.", action_kind="ex_right", prior_close="100.00",
        reference_price="98.765", rights_dividend_value="1.235", limit_up_price="108.60",
        limit_down_price="88.90", opening_auction_basis="98.765",
        dividend_adjusted_reference_price="98.765", provider="twse_twt49u", currency="TWD",
    )
    return TwmdExRightDividendRead(
        instrument_id=instrument_id, endpoint="/api/v1/ex-right-dividend-results",
        start_date="2024-06-25", end_date="2024-06-25", data=[row], status="available",
        reason="realized_events_returned",
    )


def _capital_read(instrument_id="TWSE:123A"):
    row = TwmdCapitalReductionObservation(
        recovery_date="2024-07-01", instrument_id=instrument_id, symbol=instrument_id.split(":")[1],
        observed_name="Synthetic Co.", reduction_reason="loss_offset", pre_suspension_close="50.00",
        recovery_reference_price="62.5000", limit_up_price="68.70", limit_down_price="56.30",
        opening_auction_basis="62.5000", ex_right_reference_price=None,
        provider="twse_twtauu", currency="TWD",
    )
    return TwmdCapitalReductionRead(
        instrument_id=instrument_id, endpoint="/api/v1/capital-reduction-results",
        start_date="2024-07-01", end_date="2024-07-01", data=[row], status="available",
        reason="realized_events_returned",
    )


class ActionClient:
    config = {"timeout_sec": 5}

    def __init__(self, *, capital_error=False):
        self.calls = []
        self.capital_error = capital_error

    def ex_right_dividend_results(self, instrument_id, start, end, **kwargs):
        self.calls.append(("ex", instrument_id, start, end))
        return _ex_read(instrument_id)

    def capital_reduction_results(self, instrument_id, start, end, **kwargs):
        self.calls.append(("capital", instrument_id, start, end))
        if self.capital_error:
            raise TwmdReadError("offline synthetic capital failure", reason_code="transport_error")
        return _capital_read(instrument_id)


def test_research_reads_independent_sources_and_preserves_unknown_coverage():
    client = ActionClient(capital_error=True)
    service = taiwan_research.TaiwanResearchService(
        client=client, config={"base_url": "http://offline-actions-service"}
    )
    taiwan_research.clear_taiwan_research_cache()
    block = service.corporate_actions(
        "TWSE:123A", start_date="2024-06-25", end_date="2024-07-01", today_taipei=date(2024, 7, 2)
    )

    assert block.status == "partial"
    assert block.data["instrument_id"] == "TWSE:123A"
    assert block.data["ex_right_dividend"]["data"][0]["reference_price"] == "98.765"
    assert block.data["capital_reduction"]["status"] == "error"
    assert block.data["known_event_dates"] == [
        {"date": "2024-06-25", "kind": "ex_right", "dataset": "TWT49U"}
    ]
    assert block.evidence["dataset_coverage"] == "unknown"
    assert "an empty list is unknown" in block.evidence["coverage_reason"]
    assert block.data["price_interpretation"]["rights_dividend_value_is_combined_adjustment_not_cash_dividend"]
    assert block.data["price_interpretation"]["announcement_time"] is None
    assert [call[0] for call in client.calls] == ["ex", "capital"]


def test_research_clips_read_to_each_product_floor_and_marks_pre_history_unknown():
    client = ActionClient()
    service = taiwan_research.TaiwanResearchService(
        client=client, config={"base_url": "http://offline-actions-floor"}
    )
    taiwan_research.clear_taiwan_research_cache()
    block = service.corporate_actions(
        "TWSE:123A", start_date="2003-05-05", end_date="2003-05-06", today_taipei=date(2003, 5, 7)
    )
    assert block.data["capital_reduction"]["status"] == "unknown"
    assert block.data["capital_reduction"]["reason"] == "outside_product_history"
    assert [call[0] for call in client.calls] == ["ex"]
    assert client.calls[0][2] == date(2003, 5, 5)


def test_chart_marks_event_in_daily_weekly_monthly_bucket_and_labels_raw_prices():
    bars = [
        KlineData(date="2024-06-24", open=100, high=101, low=99, close=100, volume=1000),
        KlineData(date="2024-06-25", open=99, high=100, low=98, close=99, volume=1100),
        KlineData(date="2024-06-28", open=99, high=100, low=98, close=100, volume=1200),
        KlineData(date="2024-07-01", open=100, high=101, low=99, close=100, volume=1300),
    ]
    block = {
        "status": "available",
        "data": {
            "instrument_id": "TWSE:123A",
            "ex_right_dividend": {"data": [{
                "effective_date": "2024-06-25", "instrument_id": "TWSE:123A", "symbol": "123A",
                "action_kind": "ex_right", "prior_close": "100.00", "reference_price": "98.765",
            }]},
            "capital_reduction": {"data": [{
                "recovery_date": "2024-07-01", "instrument_id": "TWSE:123A", "symbol": "123A",
                "reduction_reason": "loss_offset", "pre_suspension_close": "50.00",
                "recovery_reference_price": "62.5000",
            }]},
        },
    }
    html_by_period = {
        period: taiwan_chart_html(
            "123A", "<Synthetic & Co>", bars, period,
            instrument_id="TWSE:123A", corporate_actions=block,
        )
        for period in ("daily", "weekly", "monthly")
    }
    for html in html_by_period.values():
        assert "TWSE:123A" in html
        assert "rights/dividend adjustment is not cash" not in html
        assert "權息合併調整值不是現金股利" in html
        assert "原始日 K 未作回溯調整" in html
        assert "&lt;Synthetic &amp; Co&gt;" in html
        assert "<Synthetic & Co>" not in html
    assert html_by_period["daily"].count("stroke-dasharray=\"4 4\"") == 2
    assert html_by_period["weekly"].count("stroke-dasharray=\"4 4\"") == 2
    assert html_by_period["monthly"].count("stroke-dasharray=\"4 4\"") == 2



def test_shared_collect_retries_partial_action_failure_and_reuses_successful_data():
    class Client(ActionClient):
        def get_response(self, path, **kwargs):
            assert path == "instruments"
            return [{"instrument_id": "TWSE:1234", "venue": "TWSE", "symbol": "1234", "security_type": "EQUITY", "is_active": True, "name": "Fixture"}], {}

    client = Client(capital_error=True)
    service = taiwan_research.TaiwanResearchService(client=client, config={"base_url": "http://offline-action-retry"})
    taiwan_research.clear_taiwan_research_cache()
    selectors = dict(start_date="2024-06-25", end_date="2024-07-01", start_month="2024-06", end_month="2024-06", today_taipei=date(2024, 7, 2))
    first = service.collect("TWSE:1234", **selectors)
    assert first["blocks"]["corporate_actions"]["status"] == "partial"
    client.capital_error = False
    second = service.collect("TWSE:1234", **selectors)
    assert second["blocks"]["corporate_actions"]["status"] == "available"
    assert len(client.calls) == 4
    second["blocks"]["corporate_actions"]["data"]["known_event_dates"].clear()
    third = service.collect("TWSE:1234", **selectors)
    assert len(third["blocks"]["corporate_actions"]["data"]["known_event_dates"]) == 2
    assert len(client.calls) == 4
    taiwan_research.clear_taiwan_research_cache()


def test_chart_rejects_wrong_venue_annotations_but_keeps_successful_partial_events():
    from dataclasses import asdict
    from src.modules.research.twmd_corporate_actions import corporate_actions_block
    from src.platform.marketdata.collectors.screenshot_collector import _taiwan_chart_identity

    assert _taiwan_chart_identity("TPEX:1234", "TWSE:1234") == "TPEX:1234"
    assert _taiwan_chart_identity("TWSE:1234", "TPEX:1234") == "TWSE:1234"
    assert _taiwan_chart_identity("1234", "TWSE:9999") is None
    assert _taiwan_chart_identity("1234", "TPEX:1234") == "TPEX:1234"
    bars = [KlineData("2024-06-25", 100, 99, 101, 98, 1000)]
    block = asdict(corporate_actions_block(
        "TWSE:123A", "2024-06-25", "2024-06-25", ex_right=_ex_read(),
        capital_reduction_error=TwmdReadError("private detail", reason_code="transport_error"),
    ))
    wrong = taiwan_chart_html("TPEX:123A", "Fixture", bars, instrument_id="TPEX:123A", corporate_actions=block)
    assert 'stroke-dasharray="4 4"' not in wrong
    good = taiwan_chart_html("TWSE:123A", "Fixture", bars, instrument_id="TWSE:123A", corporate_actions=block)
    assert good.count('stroke-dasharray="4 4"') == 1
    assert "98.765" in good
    assert "部分來源讀取失敗" in good
    assert "&amp;gt;" not in good



def test_screenshot_pipeline_injects_action_reads_without_overriding_explicit_venue(monkeypatch):
    import asyncio
    from dataclasses import asdict
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector
    from src.modules.research.twmd_corporate_actions import corporate_actions_block

    bar_calls, action_calls, contents = [], [], []
    def bars(_self, symbol, **kwargs):
        bar_calls.append(symbol)
        return [KlineData("2024-06-25", 100, 99, 101, 98, 1000)]
    monkeypatch.setattr(KlineCollector, "get_klines", bars)
    async def load(identity, start, end):
        action_calls.append((identity, start, end))
        return asdict(corporate_actions_block(identity, start, end, ex_right=_ex_read(identity)))
    class Page:
        async def set_content(self, html): contents.append(html)
        async def evaluate(self, _js): pass
        async def screenshot(self, **kwargs): assert kwargs["full_page"] is True
    class Context:
        async def new_page(self): return Page()
        async def close(self): pass
    class Browser:
        async def new_context(self, **kwargs): return Context()
    collector = ScreenshotCollector(corporate_action_loader=load)
    collector._browser = Browser()
    twse = asyncio.run(collector._capture_twmd("TWSE:1234", "Fixture", "daily", instrument_id="TPEX:1234"))
    tpex = asyncio.run(collector._capture_twmd("TPEX:1234", "Fixture", "daily", instrument_id="TWSE:1234"))
    assert twse and tpex
    assert bar_calls == ["TWSE:1234", "TPEX:1234"]
    assert action_calls == [("TWSE:1234", "2024-06-25", "2024-06-25")]
    assert twse.corporate_actions["data"]["instrument_id"] == "TWSE:1234"
    assert tpex.corporate_actions["status"] == "unsupported"
