"""新聞速遞 Agent - 自選股相關新聞摘要"""

import logging
import re
from datetime import datetime
from pathlib import Path

from src.modules.automation.base import BaseAgent, AgentContext, AnalysisResult
from src.platform.marketdata.collectors.news_collector import NewsCollector, NewsItem
from src.modules.research.analysis_history import save_analysis
from src.platform.marketdata.cn_symbol import get_cn_prefix
from src.modules.automation.suggestion_pool import save_suggestion
from src.modules.research.signals import SignalPackBuilder
from src.modules.research.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "news_digest.txt"

# 新聞速遞建議型別對映（偏“訊息面”）
NEWS_ACTION_MAP = {
    "設定預警": {"action": "alert", "label": "設定預警"},
    "關注": {"action": "watch", "label": "關注"},
    "繼續持有": {"action": "hold", "label": "繼續持有"},
    "考慮減碼": {"action": "reduce", "label": "考慮減碼"},
    "暫時迴避": {"action": "avoid", "label": "暫時迴避"},
}


class NewsDigestAgent(BaseAgent):
    """新聞速遞 Agent"""

    name = "news_digest"
    display_name = "新聞速遞"
    description = "定時抓取與持倉相關的新聞資訊並推送摘要"

    def __init__(self, since_hours: int = 12, fallback_since_hours: int = 24):
        """
        Args:
            since_hours: 獲取最近 N 小時的新聞
            fallback_since_hours: 當近 N 小時無新聞時，自動回退到更長時間窗（避免“空跑”）
        """
        self.since_hours = since_hours
        self.fallback_since_hours = fallback_since_hours

    def _dedupe_with_db(self, items: list[NewsItem]) -> list[NewsItem]:
        """使用 NewsCache 表去重（跨程式/重啟也有效），避免重複推送同一條新聞。"""
        if not items:
            return []

        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import NewsCache

        db = SessionLocal()
        try:
            by_source: dict[str, list[str]] = {}
            for it in items:
                if not it.external_id:
                    continue
                by_source.setdefault(it.source, []).append(it.external_id)

            existing: set[tuple[str, str]] = set()
            for source, ids in by_source.items():
                if not ids:
                    continue
                rows = (
                    db.query(NewsCache.external_id)
                    .filter(NewsCache.source == source, NewsCache.external_id.in_(ids))
                    .all()
                )
                existing.update((source, r[0]) for r in rows)

            new_items: list[NewsItem] = []
            for it in items:
                if it.external_id and (it.source, it.external_id) in existing:
                    continue

                new_items.append(it)
                if it.external_id:
                    # 寫入快取表（內容適度截斷，避免膨脹）
                    try:
                        db.add(
                            NewsCache(
                                source=it.source,
                                external_id=it.external_id,
                                title=it.title or "",
                                content=(it.content or "")[:2000],
                                publish_time=it.publish_time,
                                symbols=it.symbols or [],
                                importance=it.importance or 0,
                            )
                        )
                    except Exception:
                        # 單條寫入失敗不影響本次返回
                        pass

            db.commit()
            return new_items
        except Exception as e:
            logger.warning(f"NewsCache 去重失敗，回退為不去重: {e}")
            db.rollback()
            return items
        finally:
            db.close()

    async def collect(self, context: AgentContext) -> dict:
        """採集新聞（自選股相關 + 重要市場新聞）"""
        symbols = [stock.symbol for stock in context.watchlist]

        if not symbols:
            logger.warning("自選股列表為空，跳過新聞採集")
            return {"news": [], "related_news": [], "watchlist": []}

        collector = NewsCollector.from_database()
        since_hours_used = self.since_hours
        news_list = await collector.fetch_all(
            symbols=symbols,
            since_hours=self.since_hours,
        )
        if (
            not news_list
            and self.fallback_since_hours
            and self.fallback_since_hours > self.since_hours
        ):
            logger.info(
                f"近 {self.since_hours} 小時無新聞，回退到近 {self.fallback_since_hours} 小時"
            )
            since_hours_used = self.fallback_since_hours
            news_list = await collector.fetch_all(
                symbols=symbols,
                since_hours=self.fallback_since_hours,
            )

        # 跨次去重：只保留“新新聞”，避免 agent 看起來一直在重複同樣內容
        news_list = self._dedupe_with_db(news_list)

        # 分類：自選股相關 + 重要市場新聞
        related_news = self._filter_related_news(news_list, symbols)
        important_news = [
            n for n in news_list if n.importance >= 2 and n not in related_news
        ]

        # 結構化訊號：補充行情/技術/資金/持倉，提高“建議摘要”穩定性
        packs = {}
        try:
            builder = SignalPackBuilder()
            sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
            packs = await builder.build_for_symbols(
                symbols=sym_list,
                include_news=False,
                news_hours=self.since_hours,
                portfolio=context.portfolio,
                include_technical=True,
                include_capital_flow=True,
                include_events=True,
                events_days=3,
            )
        except Exception as e:
            logger.warning(f"SignalPack 獲取失敗（news_digest 繼續執行）：{e}")

        return {
            "news": news_list,  # 全部新聞
            "related_news": related_news,  # 自選股相關
            "important_news": important_news,  # 重要市場新聞
            "watchlist": context.watchlist,
            "signal_packs": packs,
            "timestamp": datetime.now().isoformat(),
            "since_hours_used": since_hours_used,
        }

    def _filter_related_news(
        self, news_list: list[NewsItem], symbols: list[str]
    ) -> list[NewsItem]:
        """過濾與自選股相關的新聞"""
        related = []
        for news in news_list:
            # 新聞已標註股票
            if news.symbols and any(s in symbols for s in news.symbols):
                related.append(news)
                continue
            # 檢查標題/內容是否包含股票程式碼
            text = news.title + news.content
            if any(s in text for s in symbols):
                related.append(news)

        return related

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """構建新聞速遞 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        since_hours_used = data.get("since_hours_used") or self.since_hours
        lines.append(f"## 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## 時間窗：近 {since_hours_used} 小時\n")

        # 自選股列表（標記持倉）
        lines.append("## 自選股")
        watchlist_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        for stock in context.watchlist:
            pack = packs.get(stock.symbol)
            position = context.portfolio.get_aggregated_position(stock.symbol)

            extra_parts = []
            if pack and pack.quote:
                try:
                    extra_parts.append(
                        f"現價{pack.quote.current_price:.2f}({pack.quote.change_pct:+.2f}%)"
                    )
                except Exception:
                    pass
            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                if tech.get("trend"):
                    extra_parts.append(f"趨勢{tech.get('trend')}")
                if tech.get("macd_status"):
                    extra_parts.append(f"MACD {tech.get('macd_status')}")
            flow = (pack.capital_flow if pack else None) or {}
            if flow and not flow.get("error") and flow.get("status"):
                extra_parts.append(f"資金{flow.get('status')}")

            extra = (" | " + " ".join(extra_parts)) if extra_parts else ""
            if position:
                lines.append(
                    f"- {stock.name}({stock.symbol}) [持倉{position['total_quantity']}股]{extra}"
                )
            else:
                lines.append(f"- {stock.name}({stock.symbol}){extra}")

        # 自選股相關新聞
        related_news: list[NewsItem] = data.get("related_news", [])
        lines.append(f"\n## 自選股相關新聞 ({len(related_news)} 條)")
        if related_news:
            for news in related_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- 暫無自選股相關新聞")

        # 重要市場新聞
        important_news: list[NewsItem] = data.get("important_news", [])
        lines.append(f"\n## 重要市場新聞 ({len(important_news)} 條)")
        if important_news:
            for news in important_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- 暫無重要市場新聞")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _format_news_item(
        self, lines: list[str], news: NewsItem, watchlist_map: dict
    ) -> None:
        """格式化單條新聞"""
        importance_label = ["", "[一般]", "[重要]", "[重大]"][min(news.importance, 3)]
        time_str = news.publish_time.strftime("%H:%M")
        source_label = {"sina": "新浪", "eastmoney": "東財"}.get(
            news.source, news.source
        )

        # 關聯股票名稱
        stock_names = []
        for symbol in news.symbols:
            if symbol in watchlist_map:
                stock_names.append(watchlist_map[symbol].name)
        stock_info = f"[{','.join(stock_names)}] " if stock_names else ""

        link = f" ([原文]({news.url}))" if news.url else ""
        lines.append(
            f"- {importance_label} [{source_label} {time_str}] {stock_info}{news.title}{link}"
        )
        if news.content:
            content_brief = news.content[:200] + (
                "..." if len(news.content) > 200 else ""
            )
            lines.append(f"  > {content_brief}")

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
            sym = (getattr(s, "symbol", "") or "").strip()
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

        action_texts = list(NEWS_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) 優先匹配「...」/【...】裡的程式碼
            m = re.search(
                r"[「【\[]\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*[」】\]]",
                line,
            )
            sym_raw = m.group("sym") if m else ""

            # 2) 再匹配括號裡的程式碼（如 騰訊控股(00700)）
            if not sym_raw:
                m = re.search(
                    r"\(\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*\)", line
                )
                sym_raw = m.group("sym") if m else ""

            # 3) 再匹配行首程式碼
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) 包含方式兜底
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
                canonical = symbol_map.get(sym_key)

            if not canonical or canonical not in symbol_set:
                continue

            # 提取理由：從“建議型別”後擷取
            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[：:：\\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = NEWS_ACTION_MAP.get(
                action_text, {"action": "watch", "label": "關注"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:140],
                "should_alert": action_info["action"] in ["alert", "reduce", "sell"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (getattr(s, "symbol", "") or "").strip()
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
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "watch").strip()
            action_label = (it.get("action_label") or "關注").strip()
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
                "should_alert": action in ["alert", "reduce", "sell"],
            }
        return suggestions

    async def should_notify(self, result: AnalysisResult) -> bool:
        """有自選股相關新聞或重要市場新聞時通知"""
        related_news = result.raw_data.get("related_news", [])
        important_news = result.raw_data.get("important_news", [])

        # 有自選股相關新聞
        if related_news:
            return True
        # 有重要市場新聞
        if important_news:
            return True
        return False

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """重寫分析：落庫到歷史，便於在 UI 中檢視“新聞速遞”產物。"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

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

        # 解析個股建議並寫入建議池
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
            suggestions = self._parse_suggestions(result.content, context.watchlist)
        result.raw_data["suggestions"] = suggestions
        stock_map = {s.symbol: s for s in context.watchlist}
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if not stock:
                continue
            save_suggestion(
                stock_symbol=symbol,
                stock_name=stock.name,
                action=sug["action"],
                action_label=sug["action_label"],
                signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                reason=sug.get("reason", ""),
                agent_name=self.name,
                agent_label=self.display_name,
                expires_hours=12,
                prompt_context=user_content,
                ai_response=result.content,
                stock_market=stock.market.value,
                meta={
                    "source": "news_digest",
                    "since_hours_used": data.get("since_hours_used", self.since_hours),
                    "related_count": len(data.get("related_news", []) or []),
                    "important_count": len(data.get("important_news", []) or []),
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

        # 儲存到歷史記錄（使用 "*" 表示全域性）
        related_news: list[NewsItem] = data.get("related_news", []) or []
        important_news: list[NewsItem] = data.get("important_news", []) or []
        payload_news = []
        for it in (related_news + important_news)[:30]:
            payload_news.append(
                {
                    "source": it.source,
                    "external_id": it.external_id,
                    "title": it.title,
                    "publish_time": it.publish_time.isoformat(),
                    "symbols": it.symbols,
                    "importance": it.importance,
                    "url": it.url,
                }
            )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "since_hours": self.since_hours,
                "since_hours_used": data.get("since_hours_used", self.since_hours),
                "related_count": len(related_news),
                "important_count": len(important_news),
                "news": payload_news,
                "suggestions": suggestions,
                "prompt_context": user_content[:2000],
            },
        )

        return result
