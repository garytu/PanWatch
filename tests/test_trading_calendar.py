"""交易日曆與非交易日通知守衛單元測試。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.platform.scheduling import trading_calendar as tc
from src.platform.marketdata.models import MARKETS, MarketCode

# 2026 年真實日曆切片:8/8 週六、8/9 週日休市;8/10 週一開市;
# 10/1~10/8 國慶休市(其中 10/1 是週四 —— 工作日卻休市,只靠週末判斷抓不到)。
_FAKE_CN_DATES = frozenset(
    {
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 10),
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
        date(2026, 8, 14),
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 9),
    }
)


@pytest.fixture(autouse=True)
def _reset_calendar():
    """每個用例前後清空日曆快取,避免互相汙染。"""
    tc.reset_cache()
    yield
    tc.reset_cache()


@pytest.fixture
def loaded_calendar(monkeypatch):
    """注入固定 A 股交易日曆(不走網路)。"""
    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", lambda: _FAKE_CN_DATES)
    assert tc.refresh_blocking() is True


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


def test_週末不是交易日_無需日曆():
    """週末即使沒有日曆也判為非交易日(零依賴、永遠準確)。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 8)) is False  # 週六
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 9)) is False  # 週日
    assert tc.is_trading_day(MarketCode.HK, date(2026, 8, 8)) is False
    assert tc.is_trading_day(MarketCode.US, date(2026, 8, 9)) is False


def test_工作日是交易日(loaded_calendar):
    """日曆已載入時,普通工作日判為交易日。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 10)) is True  # 週一


def test_法定節假日不是交易日(loaded_calendar):
    """國慶(10/1 週四)靠日曆識別為休市 —— 週末判斷抓不到這一類。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is False
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 2)) is False
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 9)) is True  # 節後首個交易日


def test_日曆缺失時降級為只判週末():
    """拿不到日曆時工作日一律視為交易日 —— 寧可多跑,不可漏發一整天。"""
    assert tc._CN_TRADING_DATES is None
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is True  # 降級:識別不出國慶
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 8)) is False  # 但週末照樣攔住


def test_超出日曆覆蓋範圍時降級為只判週末(loaded_calendar):
    """查詢日期超出日曆區間(如跨年未重新整理)時降級,不誤判交易日為休市。"""
    assert tc.is_trading_day(MarketCode.CN, date(2027, 3, 1)) is True  # 2027-03-01 是週一


def test_港美股無日曆源_只判週末(loaded_calendar):
    """A 股日曆不套用到港美股(節假日不同),它們只判週末。"""
    # 10/1 對港股/美股不是中國法定假日,不應被 A 股日曆誤傷
    assert tc.is_trading_day(MarketCode.US, date(2026, 10, 1)) is True
    assert tc.is_trading_day(MarketCode.HK, date(2026, 10, 1)) is True


def test_接受字串市場碼與datetime(loaded_calendar):
    """market 接受字串,日期接受 datetime(按市場時區歸到當地日)。"""
    assert tc.is_trading_day("CN", date(2026, 10, 1)) is False
    dt = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert tc.is_trading_day("CN", dt) is False


def test_any_market_trading_day(loaded_calendar):
    """週末三市場全休 → False;工作日至少一個開市 → True。"""
    assert tc.any_market_trading_day(date(2026, 8, 8)) is False  # 週六
    assert tc.any_market_trading_day(date(2026, 8, 10)) is True  # 週一
    # A股國慶休市但美股開市 → 仍為 True
    assert tc.any_market_trading_day(date(2026, 10, 1)) is True


def test_重新整理失敗不拋異常且保持降級(monkeypatch):
    """日曆拉取拋異常時 refresh 返回 False,快取保持空,行為降級而非崩潰。"""

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", _boom)
    assert tc.refresh_blocking() is False
    assert tc._CN_TRADING_DATES is None
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 10)) is True


def test_非同步重新整理不阻塞(monkeypatch):
    """refresh() 走 to_thread,結果與同步版一致。"""
    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", lambda: _FAKE_CN_DATES)
    assert asyncio.run(tc.refresh()) is True
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is False


# ---------------------------------------------------------------------------
# is_trading_time 複用交易日曆(一處修復,全線受益)
# ---------------------------------------------------------------------------


def test_交易時段判斷在法定節假日返回False(loaded_calendar):
    """節假日的 10:00 處在時段區間內,但不是交易日 → 非交易時間。"""
    md = MARKETS[MarketCode.CN]
    holiday_10am = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(holiday_10am) is False


def test_交易時段判斷在正常交易日返回True(loaded_calendar):
    """交易日 10:00 在時段內 → 交易中。"""
    md = MARKETS[MarketCode.CN]
    trading_10am = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(trading_10am) is True


def test_交易日的非時段時間返回False(loaded_calendar):
    """交易日的 08:00 不在時段內 → 非交易時間。"""
    md = MARKETS[MarketCode.CN]
    before_open = datetime(2026, 8, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(before_open) is False


# ---------------------------------------------------------------------------
# 模擬交易定時通知的非交易日守衛(使用者報告的 bug)
# ---------------------------------------------------------------------------


def _patch_notifiers(monkeypatch) -> dict[str, int]:
    """把兩個通知函式替換成計數器,用於斷言是否被呼叫。"""
    calls = {"premarket": 0, "summary": 0}

    async def _fake_premarket():
        calls["premarket"] += 1

    async def _fake_summary():
        calls["summary"] += 1

    monkeypatch.setattr(
        "src.modules.paper_trading.paper_trading_notifier.send_premarket_plan", _fake_premarket
    )
    monkeypatch.setattr(
        "src.modules.paper_trading.paper_trading_notifier.send_daily_summary", _fake_summary
    )
    return calls


def test_週末不發盤前計劃和日終摘要(monkeypatch):
    """週末兩條模擬交易定時通知都必須跳過 —— 這是使用者報告的 bug。"""
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    saturday = datetime(2026, 8, 8, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_法定節假日不發盤前計劃和日終摘要(monkeypatch, loaded_calendar):
    """A股國慶期間(美股也休市的那幾天)同樣跳過。"""
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    # 10/3 是週六:三市場全休 → 必須跳過
    holiday = datetime(2026, 10, 3, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: holiday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_交易日照常發盤前計劃和日終摘要(monkeypatch, loaded_calendar):
    """交易日不受守衛影響,通知照常傳送。"""
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    monday = datetime(2026, 8, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 1, "summary": 1}


# ---------------------------------------------------------------------------
# 機會重新整理的非交易日守衛(週末重算全市場只是白燒資源)
# ---------------------------------------------------------------------------


def test_週末跳過機會重新整理(monkeypatch):
    """週末不重算機會池 —— 行情沒變,掃全市場純屬浪費。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 0}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 0


def test_交易日照常重新整理機會(monkeypatch, loaded_calendar):
    """交易日機會重新整理不受守衛影響。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 3, "snapshot_date": "2026-08-10"}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    monday = datetime(2026, 8, 10, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 1


def test_手動重新整理機會不受非交易日守衛影響(monkeypatch):
    """手動觸發是使用者顯式意圖,週末也必須能跑。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 1}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched.refresh_opportunities_once())

    assert calls["n"] == 1
