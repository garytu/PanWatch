"""技術分析 Agent - 多模態 K 線圖分析"""

import logging
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.platform.marketdata.collectors.screenshot_collector import ScreenshotCollector, ChartScreenshot
from src.modules.research.signals import SignalPackBuilder

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "chart_analyst.txt"


class ChartAnalystAgent(BaseAgent):
    """
    技術分析 Agent

    使用多模態 AI 分析 K 線圖截圖，輸出技術分析報告。
    需要支援 Vision 的 AI 模型（如 GPT-4V、GLM-4V 等）。
    """

    name = "chart_analyst"
    display_name = "技術分析"
    description = "擷取 K 線圖並使用多模態 AI 進行技術分析"

    def __init__(self, period: str = "daily"):
        """
        Args:
            period: K線週期 (daily/weekly/monthly)
        """
        self.period = period
        self._collector: ScreenshotCollector | None = None

    async def collect(self, context: AgentContext) -> dict:
        """採集自選股 K 線圖截圖"""
        if not context.watchlist:
            logger.warning("自選股列表為空，跳過截圖採集")
            return {"screenshots": [], "watchlist": []}

        # 準備股票列表
        stocks = [
            {
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market.value,
            }
            for stock in context.watchlist
        ]

        # 截圖
        self._collector = ScreenshotCollector()
        try:
            screenshots = await self._collector.capture_batch(
                stocks, period=self.period
            )

            # 結構化訊號（行情/技術/持倉），用於提示詞增強（失敗不影響截圖）
            packs = {}
            try:
                builder = SignalPackBuilder()
                sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
                packs = await builder.build_for_symbols(
                    symbols=sym_list,
                    include_news=False,
                    news_hours=12,
                    portfolio=context.portfolio,
                    include_technical=True,
                    include_capital_flow=False,
                    include_events=True,
                    events_days=3,
                )
            except Exception as e:
                logger.warning(f"SignalPack 獲取失敗（chart_analyst 繼續執行）：{e}")

            # 清理舊截圖
            self._collector.cleanup_old_screenshots(max_age_hours=24)

            return {
                "screenshots": screenshots,
                "watchlist": context.watchlist,
                "signal_packs": packs,
                "period": self.period,
                "timestamp": datetime.now().isoformat(),
            }
        finally:
            await self._collector.close()
            self._collector = None

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """構建技術分析 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        lines.append(f"## 分析時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## K線週期：{self._period_label(data.get('period', 'daily'))}\n")

        # 股票列表（含持倉資訊）
        lines.append("## 待分析股票")
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        packs = data.get("signal_packs", {}) or {}

        if screenshots:
            for i, shot in enumerate(screenshots, 1):
                pack = packs.get(shot.symbol)
                position = context.portfolio.get_aggregated_position(shot.symbol)
                if position:
                    lines.append(
                        f"{i}. {shot.name}({shot.symbol}) - 見圖{i}"
                        f" | 持倉{position['total_quantity']}股 成本{position['avg_cost']:.2f}"
                    )
                else:
                    lines.append(f"{i}. {shot.name}({shot.symbol}) - 見圖{i} | 未持倉")

                # 補充結構化技術摘要（讓多模態輸出更穩定）
                tech = (pack.technical if pack else None) or {}
                quote = pack.quote if pack else None
                brief_parts = []
                if quote:
                    try:
                        brief_parts.append(
                            f"現價{quote.current_price:.2f} 漲跌{quote.change_pct:+.2f}%"
                        )
                    except Exception:
                        pass
                if tech and not tech.get("error"):
                    if tech.get("trend"):
                        brief_parts.append(f"趨勢{tech.get('trend')}")
                    if tech.get("macd_status"):
                        brief_parts.append(f"MACD {tech.get('macd_status')}")
                    if tech.get("rsi_status") and tech.get("rsi6") is not None:
                        try:
                            brief_parts.append(
                                f"RSI {float(tech.get('rsi6')):.1f}({tech.get('rsi_status')})"
                            )
                        except Exception:
                            pass
                    if (
                        tech.get("support_m") is not None
                        and tech.get("resistance_m") is not None
                    ):
                        try:
                            brief_parts.append(
                                f"中期支撐{float(tech.get('support_m')):.2f}/壓力{float(tech.get('resistance_m')):.2f}"
                            )
                        except Exception:
                            pass
                if brief_parts:
                    lines.append(f"   - 訊號：{'；'.join(brief_parts)}")
        else:
            lines.append("- 無截圖")

        # 帳戶資金概況
        if context.portfolio.accounts:
            lines.append("\n## 資金狀況")
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(f"- 總可用資金: {total_funds:.0f}元")
                lines.append(f"- 總持倉成本: {total_cost:.0f}元")

        lines.append(
            "\n請根據上述股票的 K 線圖進行技術分析，結合持倉情況給出操作建議。"
        )

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _period_label(self, period: str) -> str:
        """週期中文標籤"""
        return {
            "daily": "日K",
            "weekly": "周K",
            "monthly": "月K",
        }.get(period, period)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """
        重寫分析方法以支援多模態

        將截圖作為圖片傳給 AI
        """
        system_prompt, user_content = self.build_prompt(data, context)

        # 收集圖片路徑
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        image_paths = [shot.filepath for shot in screenshots if shot.exists]

        if not image_paths:
            logger.warning("沒有可用的截圖，跳過分析")
            content = "未能獲取到 K 線圖截圖，請檢查網路連線或稍後重試。"
        else:
            # 呼叫多模態 AI
            logger.info(f"使用 {len(image_paths)} 張截圖進行多模態分析")
            content = await context.ai_client.chat(
                system_prompt,
                user_content,
                images=image_paths,
            )

        # 構建標題
        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        # 附 AI 模型資訊
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
            images=image_paths,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """有截圖且有內容時通知"""
        screenshots = result.raw_data.get("screenshots", [])
        return len(screenshots) > 0 and len(result.content) > 50

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        單隻模式執行：只分析指定的一隻股票

        用於逐只分析場景，每隻股票獨立截圖、分析和通知
        """
        # 過濾只保留指定股票
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("screenshots"):
                return None

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(
                        f"Agent [{self.display_name}] 通知已傳送: {stock_symbol}"
                    )
                else:
                    notify_error = notify_result.get("error") or "未知錯誤"
                    result.raw_data["notify_error"] = notify_error
                    logger.error(
                        f"Agent [{self.display_name}] 通知傳送失敗: {stock_symbol} - {notify_error}"
                    )
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
