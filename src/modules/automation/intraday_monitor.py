"""盤中監測 Agent - 即時監控持倉，AI 判斷是否需要提醒"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.research.analysis_history import get_latest_analysis, get_analysis
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import try_parse_action_json
from src.platform.marketdata.models import MarketCode, StockData, MARKETS

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """按市場判斷是否在交易時段。"""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    if market == MarketCode.CN:
        return "A股"
    if market == MarketCode.HK:
        return "港股"
    if market == MarketCode.US:
        return "美股"
    return market.value


# 標準化操作建議
SUGGESTION_TYPES = {
    "建倉": "buy",  # 新開倉位
    "加碼": "add",  # 增加現有倉位
    "減碼": "reduce",  # 減少倉位
    "出清": "sell",  # 全部賣出
    "持有": "hold",  # 維持現狀
    "觀望": "watch",  # 暫不操作
}

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "intraday_monitor.txt"


class IntradayMonitorAgent(BaseAgent):
    """
    盤中監測 Agent

    特點：
    - 單隻模式 (single): 逐只股票分析，每隻單獨傳送通知
    - AI 智慧判斷: 把股票資料發給 AI，由 AI 決定是否值得提醒
    - 通知節流: 同一股票短時間內不重複通知
    - 技術分析: 包含 K 線和技術指標
    """

    name = "intraday_monitor"
    display_name = "盤中監測"
    description = "交易時段即時監控持倉，AI 判斷是否有值得關注的訊號"

    def __init__(
        self,
        throttle_minutes: int = 30,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        event_only: bool = True,
        price_alert_threshold: float = 3.0,
        volume_alert_ratio: float = 2.0,
        stop_loss_warning: float = -5.0,
        take_profit_warning: float = 10.0,
    ):
        """
        Args:
            throttle_minutes: 同一股票通知間隔（分鐘）
            bypass_throttle: 是否跳過節流（測試用）
            bypass_market_hours: 是否跳過交易時段門禁（僅手動分析場景）
            price_alert_threshold: 漲跌幅超過閾值視為價格異動（%）
            volume_alert_ratio: 量比超過閾值視為放量異動
            stop_loss_warning: 未實現損失超過閾值觸發停損預警（%）
            take_profit_warning: 未實現獲利超過閾值觸發停利提醒（%）
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.event_only = event_only
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.stop_loss_warning = stop_loss_warning
        self.take_profit_warning = take_profit_warning

    async def collect(self, context: AgentContext) -> dict:
        """採集即時行情 + K線 + 歷史分析"""
        if not context.watchlist:
            logger.warning("自選股列表為空，跳過盤中監測")
            return {"stocks": [], "stock_data": None}

        # SignalPack: 統一結構化輸入（quote/technical/position）
        stock_config = context.watchlist[0] if context.watchlist else None
        market = stock_config.market if stock_config else MarketCode.CN
        symbol = stock_config.symbol if stock_config else ""
        name = stock_config.name if stock_config else symbol

        # 按股票所屬市場做交易時段門禁（而非全域性任一市場開盤）
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = f"當前{market_label(market)}非交易時段，已跳過執行"
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=True,
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

        # 獲取歷史分析（為 AI 提供更多上下文）
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """構建盤中分析 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 輔助函式：安全獲取數值，None 轉為預設值
        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "無股票資料"

        # 獲取所有帳戶的持倉資訊
        positions = context.portfolio.get_positions_for_stock(stock.symbol)
        style_labels = {"short": "短線", "swing": "波段", "long": "長線"}

        lines = []
        lines.append(f"## 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # 股票行情
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        change_amount = safe_num(stock.change_amount)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## 股票行情")
        lines.append(f"- 股票：{stock.name}（{stock.symbol}）")
        lines.append(f"- 現價：{current_price:.2f}")
        lines.append(f"- 漲跌幅：{change_pct:+.2f}%")
        lines.append(f"- 漲跌額：{change_amount:+.2f}")
        lines.append(f"- 今開：{open_price:.2f}")
        lines.append(f"- 最高：{high_price:.2f}")
        lines.append(f"- 最低：{low_price:.2f}")
        lines.append(f"- 昨收：{prev_close:.2f}")
        if volume > 0:
            lines.append(f"- 成交量：{volume:.0f} 手")
        if turnover > 0:
            lines.append(f"- 成交額：{turnover / 10000:.0f} 萬")

        # 系統閾值（幫助 AI 做出更穩定的“提醒/不提醒”判斷）
        # 價格異動改為相對個股自身波動率(ATR%)的自適應閾值,固定閾值作為下限/兜底。
        from src.modules.strategy.intraday_event_gate import (
            DEFAULT_ATR_K,
            adaptive_price_threshold,
            is_abnormal_move,
        )

        kline_for_atr = data.get("kline_summary") or {}
        atr_pct = kline_for_atr.get("atr_pct")
        adaptive_threshold = adaptive_price_threshold(
            atr_pct, self.price_alert_threshold, DEFAULT_ATR_K
        )

        lines.append("\n## 系統閾值")
        if atr_pct is not None and atr_pct > 0:
            lines.append(
                f"- 價格異動：|漲跌幅| ≥ max(固定閾值 {self.price_alert_threshold:.1f}%, "
                f"{DEFAULT_ATR_K:g}×ATR%={atr_pct:.2f}%)={adaptive_threshold:.2f}%"
                f"（相對個股自身波動率自適應，固定閾值為下限）"
            )
        else:
            lines.append(
                f"- 價格異動：|漲跌幅| ≥ {self.price_alert_threshold:.1f}%"
                f"（ATR 不可用，回退固定閾值）"
            )
        lines.append(f"- 量能異動：量比 ≥ {self.volume_alert_ratio:.1f}")
        lines.append(f"- 停損預警：未實現損失 ≤ {self.stop_loss_warning:.1f}%")
        lines.append(f"- 停利提醒：未實現獲利 ≥ {self.take_profit_warning:.1f}%")
        price_hit = (
            "觸發"
            if is_abnormal_move(
                change_pct,
                atr_pct,
                k=DEFAULT_ATR_K,
                fixed_threshold=self.price_alert_threshold,
            )
            else "未觸發"
        )
        lines.append(f"- 當前漲跌幅：{change_pct:+.2f}%（{price_hit}）")

        symbol_ctx = data.get("symbol_context") or {}
        quality = (symbol_ctx.get("data_quality") or {})
        if quality:
            lines.append(
                f"- 上下文質量：{quality.get('score', 0)}（即時新聞 {quality.get('realtime_news_count', 0)} 條，擴充套件新聞 {quality.get('extended_news_count', 0)} 條，歷史新聞 {quality.get('history_news_count', 0)} 條）"
            )

        layered_news = symbol_ctx.get("news") or {}
        realtime_news = layered_news.get("realtime") or []
        extended_news = layered_news.get("extended") or []
        history_news = layered_news.get("history") or []
        if realtime_news or extended_news or history_news:
            lines.append("\n## 新聞與事件上下文")
            chosen = realtime_news or extended_news or history_news
            for item in chosen[:3]:
                lines.append(
                    f"- [{item.get('time')}] {item.get('title')}（{item.get('source')}）"
                )
            hist_topic = (layered_news.get("history_topic") or {}).get("summary")
            if hist_topic:
                lines.append(f"- 歷史新聞主題：{hist_topic}")

        kline_history = symbol_ctx.get("kline_history") or {}
        if kline_history.get("available"):
            lines.append("\n## 歷史K線背景")
            lines.append(
                f"- 歷史漲跌：5日{format_num(kline_history.get('ret_5d'), 1)}% / 20日{format_num(kline_history.get('ret_20d'), 1)}% / 60日{format_num(kline_history.get('ret_60d'), 1)}%"
            )
            if kline_history.get("volatility_20d") is not None:
                lines.append(
                    f"- 波動(20日標準差)：{format_num(kline_history.get('volatility_20d'), 2)}%"
                )
            if kline_history.get("breakout_state") and kline_history.get("breakout_state") != "none":
                lines.append(f"- 突破狀態：{kline_history.get('breakout_state')}")

        # K 線和技術指標
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## 技術分析")

            # 基礎趨勢
            lines.append(f"- 趨勢：{kline.get('trend', 'N/A')}")
            lines.append(
                f"- 近5日：{kline.get('recent_5_up', 0)}漲{5 - kline.get('recent_5_up', 0)}跌"
            )
            lines.append(
                f"- 5日漲幅：{format_num(kline.get('change_5d'))}% | 20日漲幅：{format_num(kline.get('change_20d'))}%"
            )

            # MACD
            macd_info = f"MACD：{kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f"（{kline.get('macd_cross_days')}日前）"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get("rsi_status")
            rsi6 = kline.get("rsi6")
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6)：{rsi6:.1f}（{rsi_status}）")

            # KDJ
            kdj_status = kline.get("kdj_status")
            kdj_k, kdj_d, kdj_j = (
                kline.get("kdj_k"),
                kline.get("kdj_d"),
                kline.get("kdj_j"),
            )
            if kdj_status and kdj_k is not None:
                lines.append(
                    f"- KDJ：K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}（{kdj_status}）"
                )

            # 布林帶
            boll_status = kline.get("boll_status")
            boll_upper, boll_lower = kline.get("boll_upper"), kline.get("boll_lower")
            if boll_status and boll_upper is not None:
                lines.append(
                    f"- 布林帶：上軌={format_num(boll_upper)} 下軌={format_num(boll_lower)}（{boll_status}）"
                )

            # 量能
            volume_trend = kline.get("volume_trend")
            volume_ratio = kline.get("volume_ratio")
            if volume_trend:
                vol_info = f"量能：{volume_trend}"
                if volume_ratio:
                    vol_info += f"（量比={volume_ratio:.2f}）"
                lines.append(f"- {vol_info}")
                if volume_ratio:
                    vol_hit = (
                        "觸發" if volume_ratio >= self.volume_alert_ratio else "未觸發"
                    )
                    lines.append(f"- 量比閾值判斷：{vol_hit}")

            # 波動率（ATR）：個股自身波動基準，用於判斷"異動 vs 正常波動"
            atr_val = kline.get("atr")
            atr_pct_val = kline.get("atr_pct")
            if atr_pct_val is not None:
                atr_line = f"波動率：ATR={format_num(atr_val)}（ATR%={format_num(atr_pct_val)}%）"
                atr_line += (
                    f"，今日漲跌幅{change_pct:+.2f}% "
                    + (
                        "超出"
                        if abs(change_pct) >= adaptive_threshold
                        else "處於"
                    )
                    + f"自適應異動閾值{adaptive_threshold:.2f}%"
                )
                lines.append(f"- {atr_line}")

            # 均線
            lines.append(
                f"- MA5：{format_num(kline.get('ma5'))} | MA10：{format_num(kline.get('ma10'))} | MA20：{format_num(kline.get('ma20'))} | MA60：{format_num(kline.get('ma60'))}"
            )

        # 資金流向（僅A股，若可用）
        pack = data.get("signal_pack")
        flow = getattr(pack, "capital_flow", None) if pack else None
        if (
            isinstance(flow, dict)
            and flow
            and not flow.get("error")
            and flow.get("status")
        ):
            try:
                inflow = float(flow.get("main_net_inflow") or 0)
                inflow_pct = float(flow.get("main_net_inflow_pct") or 0)
                inflow_str = (
                    f"{inflow / 1e8:+.2f}億"
                    if abs(inflow) >= 1e8
                    else f"{inflow / 1e4:+.0f}萬"
                )
                lines.append("\n## 資金面")
                lines.append(
                    f"- 資金：{flow.get('status')}，主力淨流入{inflow_str}（{inflow_pct:+.1f}%）"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "無資料":
                    lines.append(f"- 5日資金：{flow.get('trend_5d')}")
            except Exception:
                pass

            # 多級支撐壓力
            support_m, resistance_m = kline.get("support_m"), kline.get("resistance_m")
            if support_m and resistance_m:
                lines.append(
                    f"- 中期支撐：{format_num(support_m)} | 中期壓力：{format_num(resistance_m)}"
                )

            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- 短期支撐：{format_num(support_s)} | 短期壓力：{format_num(resistance_s)}"
                )

            # K線形態
            kline_pattern = kline.get("kline_pattern")
            if kline_pattern:
                lines.append(f"- K線形態：{kline_pattern}")

            # 振幅
            amplitude = kline.get("amplitude")
            amplitude_avg5 = kline.get("amplitude_avg5")
            if amplitude is not None:
                amp_info = f"今日振幅：{amplitude:.2f}%"
                if amplitude_avg5 is not None:
                    amp_info += f"（5日平均：{amplitude_avg5:.2f}%）"
                lines.append(f"- {amp_info}")

        # 帳戶資金情況
        lines.append(f"\n## 帳戶資金")
        lines.append(f"- 總可用資金：{context.portfolio.total_available_funds:.0f} 元")
        for acc in context.portfolio.accounts:
            lines.append(f"  - {acc.name}：{acc.available_funds:.0f} 元")
        constraints = symbol_ctx.get("constraints") or {}
        if constraints:
            lines.append(
                f"- 單票倉位佔比：{safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}%（{constraints.get('risk_budget_hint', 'normal')}）"
            )
        memory = symbol_ctx.get("memory") or {}
        if memory:
            lines.append(
                f"- 歷史上下文記憶：近{memory.get('window_days', 30)}天質量均值{safe_num(memory.get('avg_quality_score'), 0):.1f}，趨勢{memory.get('quality_trend', 'flat')}"
            )
            if memory.get("latest_history_topic"):
                lines.append(f"- 歷史記憶主題：{memory.get('latest_history_topic')}")

        # 各帳戶持倉資訊
        if positions:
            lines.append(f"\n## 持倉情況（共 {len(positions)} 個帳戶）")
            for i, pos in enumerate(positions, 1):
                cost_price = safe_num(pos.cost_price, 1)
                pnl_pct = (
                    (current_price - cost_price) / cost_price * 100
                    if cost_price > 0
                    else 0
                )
                style_label = style_labels.get(pos.trading_style, "波段")
                market_value = current_price * pos.quantity
                # 找到對應帳戶的可用資金
                acc_funds = 0
                for acc in context.portfolio.accounts:
                    if acc.id == pos.account_id:
                        acc_funds = acc.available_funds
                        break

                lines.append(f"\n### 持倉 {i}：{pos.account_name}")
                lines.append(f"- 交易風格：{style_label}")
                lines.append(f"- 成本價：{cost_price:.2f}")
                lines.append(f"- 持倉量：{pos.quantity} 股")
                lines.append(f"- 持倉市值：{market_value:.0f} 元")
                pnl_note = ""
                if pnl_pct <= self.stop_loss_warning:
                    pnl_note = "（觸發停損預警）"
                elif pnl_pct >= self.take_profit_warning:
                    pnl_note = "（觸發停利提醒）"
                lines.append(f"- 未實現損益：{pnl_pct:+.1f}%{pnl_note}")
                lines.append(f"- 帳戶可用：{acc_funds:.0f} 元")
        else:
            lines.append("\n## 未持倉（僅關注）")
            lines.append(f"- 可用資金充足，可考慮建倉")

        # 歷史分析上下文（幫助 AI 做出更好的判斷）
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")

        if daily_analysis or premarket_analysis:
            lines.append("\n## 歷史分析參考")

            if daily_analysis:
                # 擷取與當前股票相關的部分（最多 300 字）
                content = (
                    daily_analysis[:300] + "..."
                    if len(daily_analysis) > 300
                    else daily_analysis
                )
                lines.append(f"\n### 昨日盤後分析摘要")
                lines.append(content)

            if premarket_analysis:
                content = (
                    premarket_analysis[:300] + "..."
                    if len(premarket_analysis) > 300
                    else premarket_analysis
                )
                lines.append(f"\n### 今日盤前分析摘要")
                lines.append(content)

        lines.append("\n請結合技術分析、資金情況和歷史分析，給出明確的操作建議。")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestion(self, content: str) -> dict:
        """
        從 AI 回應中解析操作建議

        Returns:
            {
                "action": "hold",  # buy/add/reduce/sell/hold/watch
                "action_label": "持有",
                "signal": "...",
                "reason": "...",
                "should_alert": True
            }
        """
        result = {
            "action": "watch",
            "action_label": "觀望",
            "signal": "",
            "reason": "",
            "should_alert": False,
        }

        # 1) Prefer JSON output (structured mode)
        obj = try_parse_action_json(content) or self._try_parse_loose_json(content)
        if obj:
            action = (obj.get("action") or "watch").strip()
            result["action"] = action
            result["action_label"] = (
                obj.get("action_label") or result["action_label"]
            ).strip()[:20]
            result["signal"] = (obj.get("signal") or "").strip()[:60]
            result["reason"] = (obj.get("reason") or "").strip()[:160]
            result["should_alert"] = action in {
                "buy",
                "add",
                "reduce",
                "sell",
                "alert",
                "avoid",
            }
            result["triggers"] = (
                obj.get("triggers") if isinstance(obj.get("triggers"), list) else []
            )
            result["invalidations"] = (
                obj.get("invalidations")
                if isinstance(obj.get("invalidations"), list)
                else []
            )
            result["risks"] = (
                obj.get("risks") if isinstance(obj.get("risks"), list) else []
            )
            return result

        # 檢查是否無需提醒
        if "[無需提醒]" in content:
            result["should_alert"] = False
            result["action"] = "hold"
            result["action_label"] = "持有"
            return result

        # 提取建議型別（從全文搜尋）
        for label, action in SUGGESTION_TYPES.items():
            if label in content:
                result["action"] = action
                result["action_label"] = label
                break

        # 提取訊號（支援多種格式）
        signal_patterns = [
            r"「訊號」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*訊號\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"訊號\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in signal_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["signal"] = match.group(1).strip()[:50]
                break

        # 提取建議內容（支援多種格式）
        suggest_patterns = [
            r"「建議」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*建議\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"建議\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in suggest_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                suggest_text = match.group(1).strip()
                # 從建議中提取操作型別
                for label, action in SUGGESTION_TYPES.items():
                    if label in suggest_text:
                        result["action"] = action
                        result["action_label"] = label
                        break
                # 如果訊號為空，使用建議內容作為訊號
                if not result["signal"]:
                    result["signal"] = suggest_text[:50]
                break

        # 提取理由（支援多種格式）
        reason_patterns = [
            r"「理由」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*理由\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"理由\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["reason"] = match.group(1).strip()[:100]
                break

        # 如果沒有提取到訊號和理由，嘗試使用整段內容的前部分
        if not result["signal"] and not result["reason"]:
            # 清理 markdown 格式後取前 100 字元
            clean_content = re.sub(r"\*\*|##|#", "", content).strip()
            # 跳過無需提醒的情況
            if not clean_content.startswith("[無需提醒]"):
                result["reason"] = clean_content[:100]

        # 最終 should_alert 判定：只在明確“建倉/加碼/減碼/出清”時提醒
        result["should_alert"] = result["action"] in {"buy", "add", "reduce", "sell"}
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """寬鬆解析 JSON 輸出，兜底相容模型異常格式。"""
        raw = (text or "").strip()
        if not raw:
            return None

        # 相容首行 "json"
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()

        # 去掉 fenced code block
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()

        # 優先直接解析，失敗則提取首個 JSON 物件片段
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if not isinstance(obj, dict):
            return None

        # 沒有關鍵欄位時不認為是建議 JSON
        keys = {"action", "action_label", "signal", "reason", "triggers", "invalidations", "risks"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, raw_content: str
    ) -> str:
        """當模型返回 JSON 時，生成可讀通知內容。"""
        action_label = suggestion.get("action_label") or "觀望"
        signal = suggestion.get("signal") or "無明顯新訊號"
        reason = suggestion.get("reason") or "請結合盤面與風控策略審慎判斷。"
        triggers = (
            suggestion.get("triggers")
            if isinstance(suggestion.get("triggers"), list)
            else []
        )
        invalidations = (
            suggestion.get("invalidations")
            if isinstance(suggestion.get("invalidations"), list)
            else []
        )
        risks = (
            suggestion.get("risks") if isinstance(suggestion.get("risks"), list) else []
        )
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        lines = [
            f"{stock.name}（{stock.symbol}）",
            f"現價：{price}  漲跌：{chg}",
            f"建議：{action_label}",
            f"訊號：{signal}",
            f"理由：{reason}",
        ]
        if triggers:
            lines.append("觸發條件：")
            lines.extend([f"- {str(x)}" for x in triggers[:3]])
        if invalidations:
            lines.append("失效條件：")
            lines.extend([f"- {str(x)}" for x in invalidations[:3]])
        if risks:
            lines.append("風險提示：")
            lines.extend([f"- {str(x)}" for x in risks[:3]])
        # 若本次並非純 JSON，附上簡短原文摘要便於核對
        if not (try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content)):
            brief = re.sub(r"\s+", " ", (raw_content or "").strip())[:200]
            if brief:
                lines.append(f"備註：{brief}")
        return "\n".join(lines)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI 分析並判斷是否需要提醒"""
        # 非交易時段跳過
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】跳過",
                content=data.get("skip_reason", "跳過執行"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")

        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】無資料",
                content="未獲取到股票資料",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)

        # 列印完整 prompt 用於除錯
        logger.info(f"=== Prompt for {stock.symbol} ===\n{user_content}")

        raw_content = await context.ai_client.chat(system_prompt, user_content)

        # 列印 AI 返回結果
        logger.info(f"=== AI Response for {stock.symbol} ===\n{raw_content}")

        # 解析操作建議
        suggestion = self._parse_suggestion(raw_content)
        content = raw_content
        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        quality_score = (
            (data.get("symbol_context") or {}).get("data_quality", {}).get("score")
        )
        # JSON/類 JSON 輸出時，統一轉換為可讀通知文本，避免管道直接推送原始 JSON
        if try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content):
            content = self._format_human_readable_content(stock, suggestion, raw_content)

        # 儲存到建議池（包含 prompt 上下文）
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=6,  # 盤中建議 6 小時有效
            prompt_context=user_content,  # 儲存 prompt 上下文
            ai_response=raw_content,  # 儲存 AI 原始回應
            stock_market=stock.market.value,
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "kline_meta": {
                    "computed_at": (data.get("kline_summary") or {}).get("computed_at"),
                    "asof": (data.get("kline_summary") or {}).get("asof"),
                },
                "event_gate": data.get("event_gate"),
                "analysis_date": analysis_date,
                "context_quality_score": quality_score,
                "plan": {
                    "triggers": suggestion.get("triggers")
                    if isinstance(suggestion, dict)
                    else [],
                    "invalidations": suggestion.get("invalidations")
                    if isinstance(suggestion, dict)
                    else [],
                    "risks": suggestion.get("risks")
                    if isinstance(suggestion, dict)
                    else [],
                },
            },
        )
        prediction_group_id = str(uuid.uuid4())
        for horizon in (1, 5):
            save_agent_prediction_outcome(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                stock_market=stock.market.value,
                prediction_date=analysis_date,
                horizon_days=horizon,
                prediction_group_id=prediction_group_id,
                action=suggestion.get("action") or "watch",
                action_label=suggestion.get("action_label") or "觀望",
                confidence=(float(quality_score) / 100.0)
                if quality_score is not None
                else None,
                trigger_price=getattr(stock, "current_price", None),
                meta={
                    "source": "intraday_monitor",
                    "reason": suggestion.get("reason", ""),
                    "signal": suggestion.get("signal", ""),
                },
            )

        save_agent_context_run(
            agent_name=self.name,
            stock_symbol=stock.symbol,
            analysis_date=analysis_date,
            context_payload={
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
            },
            quality={"score": quality_score or 0},
        )

        # 構建標題
        title = f"【{self.display_name}】{stock.name} {stock.change_pct:+.2f}%"

        # 附 AI 模型資訊
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        # 急漲/急跌聯動:滿足閾值時非同步觸發 TradingAgents 深度分析(預設關閉)
        try:
            from src.modules.automation.tradingagents.operations import try_auto_trigger
            try_auto_trigger(stock, source_agent=self.name)
        except Exception:
            logger.exception("TA 聯動觸發失敗,繼續返回 intraday 結果")

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                "symbol_context": data.get("symbol_context") or {},
                "quality_overview": data.get("quality_overview") or {},
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """檢查是否需要通知"""
        # 跳過的結果不通知
        if result.raw_data.get("skipped"):
            return False

        # AI 判斷不需要提醒
        if not result.raw_data.get("should_alert", True):
            logger.info(
                f"AI 判斷無需提醒: {result.raw_data.get('stock', {}).get('symbol')}"
            )
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data:
            return False

        symbol = stock_data.get("symbol")
        if not symbol:
            return False

        # 檢查節流（測試模式可跳過）
        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(
                    f"通知節流: {symbol} 在 {self.throttle_minutes} 分鐘內已通知"
                )
                return False
        else:
            logger.info(f"跳過節流檢查（測試模式）: {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """檢查是否可以傳送通知（未被節流）"""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            if not record:
                return True

            # 以 UTC 進行比較，避免容器/部署時區變化導致異常
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """更新節流記錄"""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                # 檢查是否是新的一天
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )

            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        單隻模式執行：只分析指定的一隻股票

        用於即時監控場景，每隻股票獨立分析和通知
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
            if not data.get("stock_data"):
                return None

            # 事件門禁僅作為上下文訊號，不阻斷 AI 分析。
            # 產品策略：建議持續重新整理，通知再由 should_alert + throttle 控制降噪。
            if self.event_only:
                try:
                    from src.modules.strategy.intraday_event_gate import check_and_update

                    stock = data.get("stock_data")
                    kline_summary = data.get("kline_summary")
                    decision = check_and_update(
                        symbol=stock_symbol,
                        change_pct=getattr(stock, "change_pct", None),
                        volume_ratio=(kline_summary or {}).get("volume_ratio"),
                        kline_summary=kline_summary,
                        price_threshold=self.price_alert_threshold,
                        volume_threshold=self.volume_alert_ratio,
                    )
                    data["event_gate"] = {
                        "reasons": decision.reasons,
                        "should_analyze": bool(decision.should_analyze),
                    }
                except Exception as e:
                    logger.debug(f"事件門禁異常，繼續分析: {e}")

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
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
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
