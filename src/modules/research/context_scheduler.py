"""上下文維護排程器：後驗評估 + 過期資料清理 + 機會自動重新整理。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.platform.marketdata.collectors.kline_collector import kline_source
from src.modules.research.context_store import cleanup_context_data
from src.modules.strategy.entry_candidates import evaluate_entry_candidate_outcomes
from src.modules.research.prediction_outcome import evaluate_pending_prediction_outcomes
from src.modules.strategy.strategy_engine import (
    evaluate_strategy_outcomes,
    rebalance_strategy_weights,
    refresh_strategy_signals,
)

logger = logging.getLogger(__name__)


class ContextMaintenanceScheduler:
    def __init__(
        self,
        timezone: str = "UTC",
        eval_interval_hours: int = 6,
        snapshot_retention_days: int = 180,
        outcome_retention_days: int = 365,
    ):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.eval_interval_hours = max(1, int(eval_interval_hours))
        self.snapshot_retention_days = max(30, int(snapshot_retention_days))
        self.outcome_retention_days = max(60, int(outcome_retention_days))
        self._evaluating = False
        self._cleaning = False
        self._refreshing = False

    async def _evaluate_job(self):
        if self._evaluating:
            logger.debug("[上下文維護] 上一輪後驗評估仍在執行，跳過本輪")
            return
        self._evaluating = True
        try:
            stats = await asyncio.to_thread(evaluate_pending_prediction_outcomes)
            level = logging.INFO if stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[上下文維護] 後驗評估完成: pending=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                stats.get("total_pending", 0),
                stats.get("eligible", 0),
                stats.get("evaluated", 0),
                stats.get("skipped_not_due", 0),
                stats.get("skipped_no_price", 0),
            )
            with kline_source("outcome_eval"):
                cand_stats = await asyncio.to_thread(
                    evaluate_entry_candidate_outcomes,
                    horizons=(1, 3, 5, 10),
                    snapshot_days=45,
                    limit=500,
                )
            level = logging.INFO if cand_stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[上下文維護] 候選後驗評估完成: total=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                cand_stats.get("total_candidates", 0),
                cand_stats.get("eligible", 0),
                cand_stats.get("evaluated", 0),
                cand_stats.get("skipped_not_due", 0),
                cand_stats.get("skipped_no_price", 0),
            )
            with kline_source("outcome_eval"):
                strategy_stats = await asyncio.to_thread(
                    evaluate_strategy_outcomes,
                    horizons=(1, 3, 5, 10),
                    snapshot_days=60,
                    limit=1200,
                )
            level = logging.INFO if strategy_stats.get("evaluated", 0) else logging.DEBUG
            logger.log(
                level,
                "[上下文維護] 策略後驗評估完成: total=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                strategy_stats.get("total_signals", 0),
                strategy_stats.get("eligible", 0),
                strategy_stats.get("evaluated", 0),
                strategy_stats.get("skipped_not_due", 0),
                strategy_stats.get("skipped_no_price", 0),
            )
            rebalance = await asyncio.to_thread(
                rebalance_strategy_weights,
                window_days=45,
                min_samples=8,
                alpha=0.35,
                regime="default",
            )
            level = logging.INFO if rebalance.get("changed", 0) else logging.DEBUG
            logger.log(
                level,
                "[上下文維護] 策略調權完成: changed=%s checked=%s skipped_low_sample=%s",
                rebalance.get("changed", 0),
                rebalance.get("checked", 0),
                rebalance.get("skipped_low_sample", 0),
            )

            # Phase 4 → 因子自校準閉環:把 IC/IR 接進每因子權重的輕量標定
            # (calibrate_all_markets 內部按市場算 IC 並據此調權,不再只是記錄)。
            try:
                from src.modules.strategy.factor_calibration import calibrate_all_markets

                fcal = await asyncio.to_thread(calibrate_all_markets)
                changed = sum(r.get("changed", 0) for r in fcal.values())
                logger.log(
                    logging.INFO if changed else logging.DEBUG,
                    "[上下文維護] 因子自校準完成: changed=%s detail=%s",
                    changed,
                    {m: r.get("changed", 0) for m, r in fcal.items()},
                )
            except Exception as fc_err:
                logger.debug("[上下文維護] 因子自校準跳過: %s", fc_err)
        except Exception as e:
            logger.exception(f"[上下文維護] 後驗評估異常: {e}")
        finally:
            self._evaluating = False

    async def _cleanup_job(self):
        if self._cleaning:
            logger.debug("[上下文維護] 上一輪清理仍在執行，跳過本輪")
            return
        self._cleaning = True
        try:
            deleted = await asyncio.to_thread(
                cleanup_context_data,
                snapshot_days=self.snapshot_retention_days,
                topic_days=self.snapshot_retention_days,
                context_run_days=self.snapshot_retention_days,
                outcome_days=self.outcome_retention_days,
            )
            # deleted 是 dict,任一欄位 >0 就是有清理動作
            has_work = bool(deleted and any(deleted.values()) if isinstance(deleted, dict) else deleted)
            level = logging.INFO if has_work else logging.DEBUG
            logger.log(level, "[上下文維護] 清理完成: %s", deleted)
        except Exception as e:
            logger.exception(f"[上下文維護] 清理異常: {e}")
        finally:
            self._cleaning = False

    async def evaluate_once(self) -> dict:
        agent_task = asyncio.to_thread(evaluate_pending_prediction_outcomes)
        candidate_task = asyncio.to_thread(
            evaluate_entry_candidate_outcomes,
            horizons=(1, 3, 5, 10),
            snapshot_days=45,
            limit=500,
        )
        strategy_eval_task = asyncio.to_thread(
            evaluate_strategy_outcomes,
            horizons=(1, 3, 5, 10),
            snapshot_days=60,
            limit=1200,
        )
        strategy_rebalance_task = asyncio.to_thread(
            rebalance_strategy_weights,
            window_days=45,
            min_samples=8,
            alpha=0.35,
            regime="default",
        )
        agent_stats, candidate_stats, strategy_eval_stats, strategy_rebalance_stats = await asyncio.gather(
            agent_task,
            candidate_task,
            strategy_eval_task,
            strategy_rebalance_task,
        )
        # 因子自校準:須在 outcome 評估之後(IC 才新鮮),不能並進上面的 gather。
        from src.modules.strategy.factor_calibration import calibrate_all_markets

        factor_calibration_stats = await asyncio.to_thread(calibrate_all_markets)
        return {
            "agent_predictions": agent_stats,
            "entry_candidates": candidate_stats,
            "strategy_outcomes": strategy_eval_stats,
            "strategy_rebalance": strategy_rebalance_stats,
            "factor_calibration": factor_calibration_stats,
        }

    async def _refresh_opportunities_job(self):
        """定時重新整理機會池（候選 + 策略訊號）。全市場休市日跳過。"""
        from src.platform.scheduling.trading_calendar import any_market_trading_day

        if not any_market_trading_day():
            logger.debug("[上下文維護] 非交易日，跳過機會重新整理")
            return
        if self._refreshing:
            logger.debug("[上下文維護] 上一輪機會重新整理仍在執行，跳過本輪")
            return
        self._refreshing = True
        try:
            with kline_source("refresh_opportunities"):
                result = await asyncio.to_thread(
                    refresh_strategy_signals,
                    rebuild_candidates=True,
                    max_inputs=500,
                    market_scan_limit=80,
                    max_kline_symbols=60,
                    limit_candidates=2000,
                )
            level = logging.INFO if result.get("count", 0) else logging.DEBUG
            logger.log(
                level,
                "[上下文維護] 機會自動重新整理完成: snapshot_date=%s count=%s",
                result.get("snapshot_date", ""),
                result.get("count", 0),
            )
        except Exception as e:
            logger.exception(f"[上下文維護] 機會自動重新整理異常: {e}")
        finally:
            self._refreshing = False

    async def refresh_opportunities_once(self) -> dict:
        """手動觸發一次機會重新整理。"""
        with kline_source("refresh_opportunities"):
            return await asyncio.to_thread(
                refresh_strategy_signals,
                rebuild_candidates=True,
                max_inputs=500,
                market_scan_limit=80,
                max_kline_symbols=60,
                limit_candidates=2000,
            )

    async def cleanup_once(self) -> dict:
        return await asyncio.to_thread(
            cleanup_context_data,
            snapshot_days=self.snapshot_retention_days,
            topic_days=self.snapshot_retention_days,
            context_run_days=self.snapshot_retention_days,
            outcome_days=self.outcome_retention_days,
        )

    async def _refresh_trading_calendar_job(self):
        """每日重新整理 A 股交易日曆。

        日曆只覆蓋到當年年底,長跑例項跨年後會超出覆蓋範圍而降級為"只判週末",
        因此每天凌晨拉一次。安排在各類盤前通知之前,保證當天判斷用的是新日曆。
        """
        from src.platform.scheduling.trading_calendar import refresh

        try:
            await refresh()
        except Exception as e:  # refresh 內部已兜異常,這裡只防意外
            logger.exception(f"[上下文維護] 交易日曆重新整理異常: {e}")

    def start(self):
        self.scheduler.add_job(
            self._evaluate_job,
            "interval",
            hours=self.eval_interval_hours,
            jitter=120,  # 錯峰,避免與 price_alert/paper_trading(60s)同刻寫 SQLite
            id="context_maintenance_evaluate",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._cleanup_job,
            "cron",
            hour=4,
            minute=15,
            jitter=120,
            id="context_maintenance_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # 交易日曆每日重新整理 —— 03:00,早於所有盤前通知
        self.scheduler.add_job(
            self._refresh_trading_calendar_job,
            "cron",
            hour=3,
            minute=0,
            jitter=120,
            id="context_maintenance_trading_calendar",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # 機會自動重新整理 —— 09:15 盤前 / 13:30 午盤 / 22:00 晚間。
        # 時間點按排程器時區(app_timezone,預設 Asia/Shanghai)解釋,與 Agent cron 語義一致。
        for job_hour, job_minute in ((9, 15), (13, 30), (22, 0)):
            self.scheduler.add_job(
                self._refresh_opportunities_job,
                "cron",
                hour=job_hour,
                minute=job_minute,
                jitter=120,  # 錯峰,避免與其它排程同刻寫 SQLite
                id=f"context_maintenance_refresh_opportunities_{job_hour:02d}{job_minute:02d}",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
        # Run a bootstrap evaluation shortly after startup to warm up outcome stats.
        self.scheduler.add_job(
            self._evaluate_job,
            "date",
            run_date=datetime.now(self.scheduler.timezone) + timedelta(seconds=15),
            id="context_maintenance_bootstrap_evaluate",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("context", self.scheduler)
        logger.info(
            "上下文維護排程器已啟動（後驗評估間隔 %sh，啟動補跑 +15s，快照保留 %s 天，後驗保留 %s 天，機會自動重新整理 09:15/13:30/22:00，交易日曆重新整理 03:00）",
            self.eval_interval_hours,
            self.snapshot_retention_days,
            self.outcome_retention_days,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("上下文維護排程器已關閉")
