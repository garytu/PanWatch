"""模擬交易排程器：60 秒間隔掃描建倉/平倉。"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.paper_trading.paper_trading_engine import ENGINE
from src.platform.scheduling.trading_calendar import any_market_trading_day
from src.platform.marketdata.models import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def _any_market_trading() -> bool:
    """CN/HK/US 任一在交易時段即為 True。全休市時行情不動,掃描可跳過(行為中性)。"""
    for m in (MarketCode.CN, MarketCode.HK, MarketCode.US):
        md = MARKETS.get(m)
        if md and md.is_trading_time():
            return True
    return False


class PaperTradingScheduler:
    def __init__(self, timezone: str = "UTC", interval_seconds: int = 60):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.interval_seconds = max(15, int(interval_seconds))
        self._running = False

    async def _scan_job(self):
        if self._running:
            logger.debug("[模擬交易] 上輪掃描仍在執行，跳過本輪")
            return
        if not _any_market_trading():
            logger.debug("[模擬交易] 全市場休市,跳過本輪掃描")
            return
        self._running = True
        try:
            result = await ENGINE.scan_once()
            opened = result.get("opened", 0)
            closed = result.get("closed", 0)
            status = result.get("status", "?")
            # 有實際開/平倉才是業務事件,否則只是心跳。
            level = logging.INFO if (opened or closed) else logging.DEBUG
            logger.log(
                level,
                "[模擬交易] 掃描完成: opened=%s closed=%s status=%s",
                opened,
                closed,
                status,
            )
        except Exception as e:
            logger.exception(f"[模擬交易] 掃描異常: {e}")
        finally:
            self._running = False

    async def _premarket_job(self):
        """盤前計劃通知。非交易日(週末/節假日)跳過。"""
        if not any_market_trading_day():
            logger.debug("[模擬交易] 非交易日,跳過盤前計劃通知")
            return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_premarket_plan
            await send_premarket_plan()
        except Exception as e:
            logger.exception(f"[模擬交易] 盤前計劃通知異常: {e}")

    async def _summary_job(self):
        """日終摘要通知。非交易日(週末/節假日)跳過。"""
        if not any_market_trading_day():
            logger.debug("[模擬交易] 非交易日,跳過日終摘要通知")
            return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_daily_summary
            await send_daily_summary()
        except Exception as e:
            logger.exception(f"[模擬交易] 日終摘要通知異常: {e}")

    def start(self):
        self.scheduler.add_job(
            self._scan_job,
            "interval",
            seconds=self.interval_seconds,
            jitter=20,  # 抖動錯峰,避免與價格提醒掃描每 60s 同刻併發寫 SQLite
            id="paper_trading_scan",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # 盤前計劃 - 每天 09:00
        self.scheduler.add_job(
            self._premarket_job,
            "cron",
            hour=9,
            minute=0,
            id="paper_trading_premarket",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # 日終摘要 - 每天 15:30
        self.scheduler.add_job(
            self._summary_job,
            "cron",
            hour=15,
            minute=30,
            id="paper_trading_summary",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("paper_trading", self.scheduler)
        logger.info(f"模擬交易排程器已啟動，掃描間隔 {self.interval_seconds}s")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("模擬交易排程器已關閉")
