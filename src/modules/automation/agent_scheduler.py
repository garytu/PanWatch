import logging
import time
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.automation.base import BaseAgent, AgentContext
from src.platform.marketdata.collectors.kline_collector import kline_source
from src.modules.automation.agent_runs import record_agent_run
from src.platform.observability.log_context import log_context
from src.platform.observability import otel
from src.platform.marketdata.models import MARKETS
from src.platform.scheduling.schedule_parser import parse_schedule

logger = logging.getLogger(__name__)


class AgentScheduler:
    """Agent 排程器"""

    def __init__(self, timezone: str = "UTC"):
        self.scheduler = AsyncIOScheduler()
        self.agents: dict[str, BaseAgent] = {}
        self.execution_modes: dict[str, str] = {}
        self.timezone = timezone
        # 改為儲存 context 構建函式，而非固定 context
        self.context_builder: Callable[[str], AgentContext] | None = None

    def set_context_builder(self, builder: Callable[[str], AgentContext]):
        """設定 context 構建函式（每次執行時動態構建）"""
        self.context_builder = builder

    def register(self, agent: BaseAgent, schedule: str, execution_mode: str = "batch"):
        """
        註冊 Agent 到排程器。

        Args:
            agent: Agent 例項
            schedule: 排程表示式
                - cron 格式: "分 時 日 月 周" (5 部分)
                - interval 格式: "interval:3m" 或 "interval:30s"
            execution_mode: 執行模式 batch/single（single 將逐只股票執行 run_single）
        """
        self.agents[agent.name] = agent
        self.execution_modes[agent.name] = execution_mode or "batch"

        # 解析排程表示式
        # cron 使用 5 段: "分 時 日 月 周"
        # 其中 day_of_week 的數字按 POSIX cron 語義(1-5=週一到週五)，會在內部做一次歸一化。
        trigger = parse_schedule(schedule, timezone=self.timezone)

        self.scheduler.add_job(
            self._run_agent,
            trigger=trigger,
            args=[agent.name],
            id=agent.name,
            name=agent.display_name,
            replace_existing=True,
        )

        logger.info(f"註冊 Agent: {agent.display_name} (schedule: {schedule})")

    # NOTE: cron/interval 解析邏輯統一放在 src/core/schedule_parser.py

    async def _run_agent(self, agent_name: str):
        """執行指定 Agent（動態構建 context）"""
        if not self.context_builder:
            logger.error("context_builder 未設定")
            return

        agent = self.agents.get(agent_name)
        if not agent:
            logger.error(f"Agent 未找到: {agent_name}")
            return

        start = time.monotonic()
        trace_id = f"sch-{agent_name}-{int(time.time() * 1000)}"
        try:
            # OTel root span(預設關閉時 no-op);與自建 trace 共用同一 trace_id 關聯。
            with otel.agent_run_span(
                agent_name, trace_id=trace_id, trigger_source="schedule"
            ), log_context(
                trace_id=trace_id,
                run_id=trace_id,
                agent_name=agent_name,
                event="agent_run",
                tags={"trigger_source": "schedule"},
            ):
                # 每次執行時動態構建 context（獲取最新配置）
                context = self.context_builder(agent_name)
                logger.info(f"[排程] 開始執行 Agent: {agent.display_name}")
                mode = self.execution_modes.get(agent_name, "batch")
                if mode == "single" and hasattr(agent, "run_single"):
                    processed = 0
                    skipped = 0
                    errors: list[str] = []
                    for stock in list(context.watchlist):
                        market_def = MARKETS.get(stock.market)
                        if market_def and not market_def.is_trading_time():
                            skipped += 1
                            logger.info(
                                f"[排程] 跳過 {agent.display_name} {stock.symbol}（{market_def.name} 非交易時段）"
                            )
                            continue
                        try:
                            with kline_source(f"agent:{agent_name}"):
                                res = await agent.run_single(context, stock.symbol)  # type: ignore[attr-defined]
                            processed += 1
                            try:
                                notify_error = (
                                    (res.raw_data or {}).get("notify_error")
                                    if res
                                    else ""
                                )
                            except Exception:
                                notify_error = ""
                            if notify_error:
                                errors.append(f"{stock.symbol} notify: {notify_error}")
                        except Exception as e:
                            logger.error(
                                f"Agent [{agent_name}] 單隻執行失敗 {stock.symbol}: {e}",
                                exc_info=True,
                            )
                            errors.append(f"{stock.symbol}: {e}")
                    logger.info(
                        f"[排程] Agent 單隻模式執行完成: {agent.display_name}（執行{processed}，跳過{skipped}，共{len(context.watchlist)}）"
                    )
                    duration_ms = int((time.monotonic() - start) * 1000)
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if errors else "success",
                        result=f"single mode executed {processed}, skipped {skipped}, total {len(context.watchlist)}",
                        error="; ".join(errors),
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        model_label=context.model_label,
                    )
                else:
                    with kline_source(f"agent:{agent_name}"):
                        result = await agent.run(context)
                    duration_ms = int((time.monotonic() - start) * 1000)
                    notify_error = ""
                    try:
                        notify_error = (result.raw_data or {}).get("notify_error") or ""
                    except Exception:
                        notify_error = ""
                    raw = result.raw_data or {}
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if notify_error else "success",
                        result=(result.content or "")[:2000],
                        error=(notify_error or "")[:2000],
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        notify_attempted=(
                            "notified" in raw
                            or "notify_error" in raw
                            or "notify_skipped" in raw
                        ),
                        notify_sent=bool(raw.get("notified", False)),
                        model_label=context.model_label,
                    )
                logger.info(f"[排程] Agent 執行完成: {agent.display_name}")
        except Exception as e:
            logger.error(f"Agent [{agent_name}] 排程執行異常: {e}", exc_info=True)
            duration_ms = int((time.monotonic() - start) * 1000)
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=duration_ms,
                trace_id=trace_id,
                trigger_source="schedule",
            )

    async def trigger_now(self, agent_name: str):
        """立即執行某個 Agent（手動觸發）"""
        await self._run_agent(agent_name)

    def start(self):
        """啟動排程器"""
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("agent", self.scheduler)
        logger.info(f"排程器已啟動，已註冊 {len(self.agents)} 個 Agent")

        # 列印所有已註冊的任務
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            logger.info(f"  - {job.name}: 下次執行 {job.next_run_time}")

    def shutdown(self):
        """關閉排程器"""
        self.scheduler.shutdown()
        logger.info("排程器已關閉")
