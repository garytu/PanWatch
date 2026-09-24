import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.modules.research.analysis_history import save_analysis
from src.platform.marketdata.cn_symbol import get_cn_prefix
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.context_store import (
    save_agent_context_run,
    save_agent_prediction_outcome,
)
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.platform.marketdata.models import MarketCode, IndexData

logger = logging.getLogger(__name__)

# 盤後建議型別對映
DAILY_ACTION_MAP = {
    "繼續持有": {"action": "hold", "label": "繼續持有"},
    "考慮加碼": {"action": "add", "label": "考慮加碼"},
    "考慮減碼": {"action": "reduce", "label": "考慮減碼"},
    "考慮停損": {"action": "sell", "label": "考慮停損"},
    "明日關注": {"action": "watch", "label": "明日關注"},
    "暫時迴避": {"action": "avoid", "label": "暫時迴避"},
}

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "daily_report.txt"

# A 股大盤指數的顯式騰訊符號（與 akshare_collector.CN_INDICES 口徑一致）
_CN_INDEX_TENCENT_SYMBOLS = ["sh000001", "sz399001", "sz399006"]


def get_market_data():
    """惰性 import,避免包未裝/迴圈 import 影響本模組載入。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()


class DailyReportAgent(BaseAgent):
    """盤後日報 Agent"""

    name = "daily_report"
    display_name = "收盤覆盤"
    description = "每日收盤後生成自選股日報，包含大盤概覽、個股分析和明日關注"

    async def _fetch_index_for_market(self, market_code: MarketCode) -> list[IndexData]:
        """按 market 取大盤指數。

        直接走 marketdata 新包(index_quotes)。
        與舊 _get_cn_index 口徑一致：僅 CN 出數，其餘市場返回空 list。
        """
        if market_code != MarketCode.CN:
            return []
        items = get_market_data().index_quotes(_CN_INDEX_TENCENT_SYMBOLS)
        return [
            IndexData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.CN,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                timestamp=datetime.now(),
            )
            for item in items
        ]

    async def collect(self, context: AgentContext) -> dict:
        """採集大盤指數 + 自選股結構化資料包（行情/技術/資金/新聞/持倉）"""

        all_indices: list[IndexData] = []
        markets = []
        seen = set()
        for s in context.watchlist:
            if s.market not in seen:
                seen.add(s.market)
                markets.append(s.market)

        for market_code in markets:
            try:
                indices = await self._fetch_index_for_market(market_code)
                all_indices.extend(indices)
            except Exception as e:
                logger.warning(f"獲取 {market_code.value} 指數失敗: {e}")

        builder = SignalPackBuilder()
        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        packs = await builder.build_for_symbols(
            symbols=sym_list,
            include_news=True,
            news_hours=72,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=7,
        )

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=24,
            extended_hours=72,
            history_days=30,
            kline_days=120,
            persist_snapshot=True,
        )

        if not all_indices and not any(p.quote for p in packs.values()):
            raise RuntimeError("資料採集失敗：未獲取到任何行情資料，請檢查網路連線")

        return {
            "indices": all_indices,
            "signal_packs": packs,
            "symbol_contexts": context_pack.get("symbols", {}),
            "quality_overview": context_pack.get("quality_overview", {}),
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """構建日報 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 輔助函式：安全獲取數值，None 轉為預設值
        def safe_num(value, default=0):
            return value if value is not None else default

        # 構建使用者輸入：結構化的市場資料
        lines = []
        lines.append(f"## 日期：{datetime.now().strftime('%Y-%m-%d')}\n")
        symbol_contexts = data.get("symbol_contexts", {}) or {}
        quality_overview = data.get("quality_overview", {}) or {}

        if quality_overview:
            lines.append("## 上下文質量概覽")
            lines.append(
                f"- 平均質量分：{quality_overview.get('avg_score', 0)}（最低 {quality_overview.get('min_score', 0)} / 最高 {quality_overview.get('max_score', 0)}）"
            )
            global_topic = (quality_overview.get("global_news_topic") or {})
            if global_topic.get("summary"):
                lines.append(f"- 歷史新聞主題：{global_topic.get('summary')}")
            lines.append("")

        # 大盤指數
        lines.append("## 大盤指數")
        for idx in data["indices"]:
            change_pct = safe_num(idx.change_pct)
            direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
            lines.append(
                f"- {idx.name}: {safe_num(idx.current_price):.2f} "
                f"{direction} {change_pct:+.2f}% "
                f"成交額:{safe_num(idx.turnover) / 1e8:.0f}億"
            )

        # 自選股詳細資訊
        lines.append("\n## 自選股詳細資訊")
        packs = data.get("signal_packs", {}) or {}

        for w in context.watchlist:
            pack = packs.get(w.symbol)
            stock_ctx = symbol_contexts.get(w.symbol, {}) or {}
            stock_quality = (stock_ctx.get("data_quality") or {})
            quote = pack.quote if pack else None
            stock_name = (w.name or (quote.name if quote else "") or w.symbol).strip()
            lines.append(f"\n### {stock_name}（{w.symbol}）")
            if stock_quality:
                lines.append(
                    f"- 資料質量：{stock_quality.get('score', 0)}（即時新聞 {stock_quality.get('realtime_news_count', 0)} 條，擴充套件新聞 {stock_quality.get('extended_news_count', 0)} 條，歷史新聞 {stock_quality.get('history_news_count', 0)} 條）"
                )

            # 基本行情
            if quote:
                change_pct = safe_num(quote.change_pct)
                direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"

                current_price = safe_num(quote.current_price)
                high_price = safe_num(quote.high_price)
                low_price = safe_num(quote.low_price)
                prev_close = safe_num(quote.prev_close, 1)  # 避免除零
                turnover = safe_num(quote.turnover)

                lines.append(
                    f"- 今日：{current_price:.2f} {direction} {change_pct:+.2f}%"
                )
                amplitude = (
                    (high_price - low_price) / prev_close * 100 if prev_close > 0 else 0
                )
                lines.append(
                    f"- 振幅：{amplitude:.1f}%  最高{high_price:.2f} 最低{low_price:.2f}"
                )
                lines.append(f"- 成交額：{turnover / 1e8:.2f}億")
            else:
                current_price = 0
                lines.append("- 今日：行情資料缺失")

            # 技術指標
            tech = (pack.technical if pack else None) or {"error": "無技術指標資料"}
            if not tech.get("error"):
                ma5 = safe_num(tech.get("ma5"))
                ma10 = safe_num(tech.get("ma10"))
                ma20 = safe_num(tech.get("ma20"))
                lines.append(f"- 均線：MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}")
                lines.append(
                    f"- 趨勢：{tech.get('trend', '未知')}，MACD {tech.get('macd_status', '未知')}"
                )
                change_5d = tech.get("change_5d")
                change_20d = tech.get("change_20d")
                if change_5d is not None:
                    lines.append(
                        f"- 近期：5日{change_5d:+.1f}% 20日{safe_num(change_20d):+.1f}%"
                    )
                if tech.get("volume_trend"):
                    vol_ratio = tech.get("volume_ratio")
                    ratio_str = (
                        f"（量比{vol_ratio:.2f}）" if vol_ratio is not None else ""
                    )
                    lines.append(f"- 量能：{tech.get('volume_trend')}{ratio_str}")
                if tech.get("rsi6") is not None and tech.get("rsi_status"):
                    lines.append(
                        f"- RSI：{tech.get('rsi6'):.1f}（{tech.get('rsi_status')}）"
                    )
                if tech.get("kdj_status"):
                    kdj_k = tech.get("kdj_k")
                    kdj_d = tech.get("kdj_d")
                    kdj_j = tech.get("kdj_j")
                    if kdj_k is not None and kdj_d is not None and kdj_j is not None:
                        lines.append(
                            f"- KDJ：{tech.get('kdj_status')}（K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}）"
                        )
                    else:
                        lines.append(f"- KDJ：{tech.get('kdj_status')}")
                if tech.get("boll_status"):
                    boll_upper = tech.get("boll_upper")
                    boll_lower = tech.get("boll_lower")
                    if boll_upper is not None and boll_lower is not None:
                        lines.append(
                            f"- 布林：{tech.get('boll_status')}（上軌{boll_upper:.2f} 下軌{boll_lower:.2f}）"
                        )
                    else:
                        lines.append(f"- 布林：{tech.get('boll_status')}")
                if tech.get("kline_pattern"):
                    lines.append(f"- 形態：{tech.get('kline_pattern')}")
                if tech.get("amplitude") is not None:
                    amp = tech.get("amplitude")
                    amp5 = tech.get("amplitude_avg5")
                    if amp5 is not None:
                        lines.append(f"- 振幅：{amp:.1f}%（5日均{amp5:.1f}%）")
                    else:
                        lines.append(f"- 振幅：{amp:.1f}%")
                support_m = tech.get("support_m")
                resistance_m = tech.get("resistance_m")
                if support_m is not None and resistance_m is not None:
                    lines.append(
                        f"- 支撐壓力：中期支撐{support_m:.2f} 中期壓力{resistance_m:.2f}"
                    )
                else:
                    support = tech.get("support")
                    resistance = tech.get("resistance")
                    if support is not None and resistance is not None:
                        lines.append(
                            f"- 支撐壓力：支撐{support:.2f} 壓力{resistance:.2f}"
                        )

            # 資金流向（僅A股）
            flow = (pack.capital_flow if pack else None) or {}
            if not flow.get("error") and flow.get("status"):
                inflow = safe_num(flow.get("main_net_inflow"))
                inflow_pct = safe_num(flow.get("main_net_inflow_pct"))
                inflow_str = (
                    f"{inflow / 1e8:+.2f}億"
                    if abs(inflow) >= 1e8
                    else f"{inflow / 1e4:+.0f}萬"
                )
                lines.append(
                    f"- 資金：{flow['status']}，主力淨流入{inflow_str}（{inflow_pct:+.1f}%）"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "無資料":
                    lines.append(f"- 5日資金：{flow['trend_5d']}")

            # 相關新聞/公告
            stock_news = (
                (stock_ctx.get("news") or {}).get("realtime")
                or (stock_ctx.get("news") or {}).get("extended")
                or (pack.news.items if (pack and pack.news) else [])
            )
            if stock_news:
                lines.append("- 相關新聞：")
                for n in stock_news[:3]:
                    source_label = {"sina": "新浪", "eastmoney": "東財"}.get(
                        n.get("source"), n.get("source")
                    )
                    importance_star = (
                        "⭐" * (n.get("importance") or 0) if n.get("importance") else ""
                    )
                    time_str = n.get("time") or ""
                    title = n.get("title") or ""
                    link = f"[原文]({n.get('url')})" if n.get("url") else ""
                    lines.append(
                        f"  - [{time_str}] {importance_star}{title}（{source_label}）{(' ' + link) if link else ''}"
                    )
            else:
                lines.append("- 相關新聞：暫無")
            history_topic = ((stock_ctx.get("news") or {}).get("history_topic") or {})
            if history_topic.get("summary"):
                lines.append(f"- 歷史新聞記憶(近30天)：{history_topic.get('summary')}")

            # 事件快照（近 N 天，來自公告結構化）
            events = pack.events.items if (pack and pack.events) else []
            important_events = [e for e in events if (e.get("importance") or 0) >= 2]
            if important_events:
                lines.append("- 事件：")
                for e in important_events[:2]:
                    time_str = e.get("time") or ""
                    et = e.get("event_type") or "notice"
                    title = e.get("title") or ""
                    link = f"[原文]({e.get('url')})" if e.get("url") else ""
                    lines.append(
                        f"  - [{time_str}] ({et}) {title}{(' ' + link) if link else ''}"
                    )

            # 持倉資訊
            position = None
            if pack and pack.position and pack.position.aggregated:
                position = pack.position.aggregated
            else:
                try:
                    position = context.portfolio.get_aggregated_position(w.symbol)
                except Exception:
                    position = None

            if position:
                total_qty = position.get("total_quantity")
                avg_cost = safe_num(position.get("avg_cost"), 1)
                pnl_pct = (
                    (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
                )
                style_labels = {"short": "短線", "swing": "波段", "long": "長線"}
                style = style_labels.get(position.get("trading_style", "swing"), "波段")
                if total_qty is not None:
                    lines.append(
                        f"- 持倉：{total_qty}股 成本{avg_cost:.2f} 未實現獲利{pnl_pct:+.1f}%（{style}）"
                    )

            kline_history = stock_ctx.get("kline_history") or {}
            if kline_history.get("available"):
                ret_5d = kline_history.get("ret_5d")
                ret_20d = kline_history.get("ret_20d")
                ret_60d = kline_history.get("ret_60d")
                lines.append(
                    "- 歷史走勢："
                    f"5日{(f'{ret_5d:+.1f}%' if ret_5d is not None else 'N/A')} "
                    f"20日{(f'{ret_20d:+.1f}%' if ret_20d is not None else 'N/A')} "
                    f"60日{(f'{ret_60d:+.1f}%' if ret_60d is not None else 'N/A')}"
                )

            constraints = stock_ctx.get("constraints") or {}
            if constraints:
                lines.append(
                    f"- 資金約束：總可用{safe_num(constraints.get('total_available_funds'), 0):.0f}元，單票倉位佔比{safe_num(constraints.get('single_position_ratio'), 0) * 100:.1f}%（{constraints.get('risk_budget_hint', 'normal')}）"
                )
            memory = stock_ctx.get("memory") or {}
            if memory:
                lines.append(
                    f"- 歷史上下文記憶：近{memory.get('window_days', 30)}天質量均值{safe_num(memory.get('avg_quality_score'), 0):.1f}，趨勢{memory.get('quality_trend', 'flat')}"
                )
                if memory.get("latest_history_topic"):
                    lines.append(f"- 歷史記憶主題：{memory.get('latest_history_topic')}")

        # 帳戶資金概況
        if context.portfolio.accounts:
            lines.append("\n## 帳戶概況")
            for acc in context.portfolio.accounts:
                if acc.positions or acc.available_funds > 0:
                    acc_cost = acc.total_cost
                    lines.append(
                        f"- {acc.name}: 持倉成本{acc_cost:.0f}元 可用資金{acc.available_funds:.0f}元"
                    )
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(
                    f"- 合計: 總持倉成本{total_cost:.0f}元 總可用資金{total_funds:.0f}元"
                )

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestions(self, content: str, watchlist: list) -> dict[str, dict]:
        """
        從 AI 回應中解析個股建議
        返回: {symbol: {action, action_label, reason, should_alert}}
        """
        suggestions: dict[str, dict] = {}
        if not content or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        name_map: dict[str, str] = {}

        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym  # 相容去掉前導 0（如 00700 -> 700）
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym
            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = get_cn_prefix(sym, upper=True)
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym
            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(DAILY_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # 快速過濾：必須包含某個建議型別
            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) 優先匹配「...」/【...】裡的程式碼
            m = re.search(r"[「【\[]\s*(?P<sym>[A-Za-z]{1,5}|\d{3,6})\s*[」】\]]", line)
            sym_raw = m.group("sym") if m else ""

            # 2) 再匹配括號裡的程式碼（如 騰訊控股(00700)）
            if not sym_raw:
                m = re.search(r"\(\s*(?P<sym>[A-Za-z]{1,5}|\d{3,6})\s*\)", line)
                sym_raw = m.group("sym") if m else ""

            # 3) 再匹配行首程式碼（如 600519 繼續持有：...）
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z]{1,5}|\d{3,6})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) 最後用“包含”方式兜底（避免 AI 輸出了帶前字尾的程式碼）
            if not sym_raw:
                for k in sorted(symbol_map.keys(), key=len, reverse=True):
                    if k and k in line.upper():
                        sym_raw = k
                        break

            # 5) 名稱兜底
            if not sym_raw:
                for name, sym in name_map.items():
                    if name and name in line:
                        sym_raw = sym
                        break

            if not sym_raw:
                continue

            sym_key = sym_raw.strip()
            canonical = symbol_map.get(sym_key.upper()) or symbol_map.get(sym_key)
            if not canonical and sym_key.isdigit():
                canonical = symbol_map.get(sym_key)  # HK 去 0 的情況

            if not canonical or canonical not in symbol_set:
                continue

            # 提取理由：從“建議型別”後擷取
            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[：:：\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = DAILY_ACTION_MAP.get(
                action_text, {"action": "hold", "label": "繼續持有"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:100],
                "should_alert": action_info["action"] in ["add", "reduce", "sell"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        """Parse suggestions from structured JSON block."""
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym
            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = get_cn_prefix(sym, upper=True)
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym

        for it in items:
            if not isinstance(it, dict):
                continue
            sym_raw = (it.get("symbol") or "").strip()
            if not sym_raw:
                continue
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "hold").strip()
            action_label = (it.get("action_label") or "繼續持有").strip()
            reason = (it.get("reason") or "").strip()
            signal = (it.get("signal") or "").strip()

            suggestions[canonical] = {
                "action": action,
                "action_label": action_label,
                "reason": reason[:160],
                "signal": signal[:60],
                "triggers": it.get("triggers")
                if isinstance(it.get("triggers"), list)
                else [],
                "invalidations": it.get("invalidations")
                if isinstance(it.get("invalidations"), list)
                else [],
                "risks": it.get("risks") if isinstance(it.get("risks"), list) else [],
                "should_alert": action in ["add", "reduce", "sell"],
            }

        return suggestions

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """呼叫 AI 分析並儲存到歷史/建議池"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        # Keep structured JSON block at the very end.
        if context.model_label:
            idx = content.rfind(TAG_START)
            if idx >= 0:
                content = (
                    content[:idx].rstrip()
                    + f"\n\n---\nAI: {context.model_label}\n\n"
                    + content[idx:]
                )
            else:
                content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        structured = try_extract_tagged_json(content) or {}
        display_content = strip_tagged_json(content)

        stock_items = [
            f"{(s.name or s.symbol).strip()}({s.symbol})"
            for s in context.watchlist[:5]
        ]
        stock_names = "、".join(stock_items) if stock_items else "無股票"
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # 解析個股建議
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
            suggestions = self._parse_suggestions(result.content, context.watchlist)
        result.raw_data["suggestions"] = suggestions

        # 儲存各股票建議到建議池
        stock_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        symbol_contexts = data.get("symbol_contexts", {}) or {}
        analysis_date = (data.get("timestamp") or "")[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if stock:
                pack = packs.get(symbol)
                trigger_price = (
                    getattr(pack.quote, "current_price", None)
                    if pack and pack.quote
                    else None
                )
                quality_score = (
                    (symbol_contexts.get(symbol, {}) or {})
                    .get("data_quality", {})
                    .get("score")
                )
                save_suggestion(
                    stock_symbol=symbol,
                    stock_name=stock.name,
                    action=sug["action"],
                    action_label=sug["action_label"],
                    signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                    reason=sug.get("reason", ""),
                    agent_name=self.name,
                    agent_label=self.display_name,
                    expires_hours=16,  # 盤後建議隔夜有效
                    prompt_context=user_content,
                    ai_response=result.content,
                    stock_market=stock.market.value,
                    meta={
                        "analysis_date": analysis_date,
                        "source": "daily_report",
                        "context_quality_score": quality_score,
                        "plan": {
                            "triggers": sug.get("triggers")
                            if isinstance(sug.get("triggers"), list)
                            else [],
                            "invalidations": sug.get("invalidations")
                            if isinstance(sug.get("invalidations"), list)
                            else [],
                            "risks": sug.get("risks")
                            if isinstance(sug.get("risks"), list)
                            else [],
                        }
                        if isinstance(sug, dict)
                        else {},
                    },
                )
                prediction_group_id = str(uuid.uuid4())
                for horizon in (1, 5):
                    save_agent_prediction_outcome(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        stock_market=stock.market.value,
                        prediction_date=analysis_date,
                        horizon_days=horizon,
                        prediction_group_id=prediction_group_id,
                        action=sug.get("action") or "hold",
                        action_label=sug.get("action_label") or "繼續持有",
                        confidence=(float(quality_score) / 100.0)
                        if quality_score is not None
                        else None,
                        trigger_price=trigger_price,
                        meta={
                            "source": "daily_report",
                            "reason": sug.get("reason", ""),
                            "signal": sug.get("signal", ""),
                        },
                    )

        # 儲存到歷史記錄（使用 "*" 表示全域性分析）
        # 簡化 raw_data，只儲存關鍵資訊
        symbols = [s.symbol for s in context.watchlist]
        compact_context = {}
        context_payload = {}
        for sym, ctx in symbol_contexts.items():
            layered_news = ctx.get("news") or {}
            events = ctx.get("events") or []
            compact_context[sym] = {
                "data_quality": ctx.get("data_quality") or {},
                "history_news_topic": ((ctx.get("news") or {}).get("history_topic"))
                or {},
                "kline_history": ctx.get("kline_history") or {},
                "constraints": ctx.get("constraints") or {},
                "memory": ctx.get("memory") or {},
            }
            context_payload[sym] = {
                "data_quality": ctx.get("data_quality") or {},
                "kline_history": ctx.get("kline_history") or {},
                "constraints": ctx.get("constraints") or {},
                "memory": ctx.get("memory") or {},
                "news": {
                    "realtime": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("realtime") or [])[:3]
                    ],
                    "extended": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("extended") or [])[:3]
                    ],
                    "history": [
                        {
                            "time": n.get("time"),
                            "title": n.get("title"),
                            "source": n.get("source"),
                            "importance": n.get("importance"),
                        }
                        for n in (layered_news.get("history") or [])[:3]
                    ],
                    "history_topic": layered_news.get("history_topic") or {},
                },
                "events": [
                    {
                        "time": e.get("time"),
                        "title": e.get("title"),
                        "event_type": e.get("event_type"),
                        "importance": e.get("importance"),
                    }
                    for e in events[:3]
                ],
            }
        quality_overview = data.get("quality_overview") or {}
        news_debug = {}
        for sym, ctx in symbol_contexts.items():
            layered = ctx.get("news") or {}
            news_debug[sym] = {
                "realtime_count": len(layered.get("realtime") or []),
                "extended_count": len(layered.get("extended") or []),
                "history_count": len(layered.get("history") or []),
            }
        save_agent_context_run(
            agent_name=self.name,
            stock_symbol="*",
            analysis_date=analysis_date,
            context_payload={
                "quality_overview": quality_overview,
                "symbols": compact_context,
            },
            quality={"score": quality_overview.get("avg_score", 0)},
        )
        history_saved = save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "symbols": symbols,
                "timestamp": data.get("timestamp"),
                "quality_overview": quality_overview,
                "context_summary": compact_context,
                "context_payload": context_payload,
                "prompt_context": user_content[:12000],
                "prompt_stats": {
                    "prompt_chars": len(user_content or ""),
                    "watchlist_count": len(context.watchlist),
                },
                "news_debug": news_debug,
                "suggestions": suggestions,
            },
        )
        if history_saved:
            logger.info(f"收盤覆盤已儲存到歷史記錄，包含 {len(suggestions)} 條建議")
        else:
            logger.error("收盤覆盤儲存歷史記錄失敗")

        return result
