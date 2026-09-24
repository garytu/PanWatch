"""Shared read-only tools used by the legacy chat endpoint and MCP adapter.

The old ``/api/chat`` route and the MCP transport expose the same assistant
capabilities.  Their schema and dispatch therefore live in this module instead
of either HTTP router, so another module can use the assistant boundary without
depending on a router implementation.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from src.modules.portfolio import build_portfolio_service
from src.platform.persistence.models import AnalysisHistory, Stock, StockSuggestion


logger = logging.getLogger(__name__)

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "獲取使用者的實盤持倉和模擬交易持倉。用於回答持倉相關問題（持倉健康嗎、該調倉嗎、損益情況等）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": "獲取某隻股票的即時行情（價格、漲跌幅、成交量等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票程式碼，如 600519"},
                    "market": {"type": "string", "description": "市場程式碼：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_technical_analysis",
            "description": "獲取股票的技術面分析（趨勢、MACD、RSI、支撐位、壓力位等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票程式碼"},
                    "market": {"type": "string", "description": "市場程式碼：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_suggestions",
            "description": "獲取某隻股票最近的 AI 建議和分析報告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票程式碼"},
                    "market": {"type": "string", "description": "市場程式碼：CN/HK/US", "default": "CN"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_watchlist",
            "description": "獲取使用者的自選股（關注列表）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def build_watchlist_context(db: Session) -> str:
    """Return the user's watchlist in a text form suitable for a tool result."""
    stocks = db.query(Stock).order_by(Stock.sort_order.asc()).all()
    if not stocks:
        return "使用者暫無自選股。"
    lines = [f"- {stock.name}({stock.market}:{stock.symbol})" for stock in stocks]
    return "自選股列表：\n" + "\n".join(lines)


def build_stock_context(db: Session, symbol: str, market: str) -> str:
    """Return the latest persisted suggestions and analysis for one stock."""
    parts: list[str] = []
    suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .limit(3)
        .all()
    )
    if suggestions:
        lines = [
            f"- [{item.agent_label or item.agent_name}] {item.action_label}: {item.signal or item.reason or ''}"
            for item in suggestions
        ]
        parts.append("最近 AI 建議：\n" + "\n".join(lines))

    histories = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.stock_symbol == symbol)
        .order_by(AnalysisHistory.created_at.desc())
        .limit(1)
        .all()
    )
    if histories:
        history = histories[0]
        parts.append(
            f"最近分析（{history.agent_name}, {history.analysis_date}）：\n{(history.content or '')[:500]}"
        )
    return "\n\n".join(parts)


def build_portfolio_context(db: Session) -> str:
    """Return the portfolio module's public assistant summary."""
    return build_portfolio_service(db).build_assistant_summary()


async def fetch_realtime_context(symbol: str, market: str) -> str:
    """Return a compact quote summary; failures degrade to an empty context."""
    try:
        from src.platform.marketdata.marketdata_client import md_quote_rows
        from src.platform.marketdata.models import MarketCode

        code = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], code.value)
        if not rows:
            return ""
        quote = rows[0]
        return (
            f"即時行情：{quote.get('name', symbol)}（{market}:{symbol}）價格 "
            f"{quote.get('current_price', '--')}，漲跌幅 {quote.get('change_pct', '--')}%，"
            f"成交量 {quote.get('volume', '--')}"
        )
    except Exception as exc:  # noqa: BLE001 - a missing quote must not fail chat
        logger.debug("獲取即時行情失敗: %s", exc)
        return ""


async def fetch_technical_context(symbol: str, market: str) -> str:
    """Return a compact technical summary; failures degrade to an empty context."""
    try:
        from src.modules.market.data_collector import DataCollector

        summary = await asyncio.to_thread(DataCollector().get_kline_summary, symbol, market)
        if not summary or summary.get("error"):
            return ""
        data = summary.get("summary", {})
        return (
            f"技術面：趨勢 {data.get('trend', '--')}，MACD {data.get('macd_status', '--')}，"
            f"RSI {data.get('rsi_14', '--')}，支撐位 {data.get('support_level', '--')}，"
            f"壓力位 {data.get('resistance_level', '--')}"
        )
    except Exception as exc:  # noqa: BLE001 - a missing indicator must not fail chat
        logger.debug("獲取技術面失敗: %s", exc)
        return ""


async def execute_chat_tool(db: Session, name: str, arguments: dict) -> str:
    """Dispatch one declared read-only assistant tool."""
    try:
        if name == "get_portfolio":
            return build_portfolio_context(db) or "使用者暫無持倉。"
        if name == "get_stock_quote":
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "CN")
            return await fetch_realtime_context(symbol, market) or f"未能獲取 {market}:{symbol} 的行情資料。"
        if name == "get_technical_analysis":
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "CN")
            return await fetch_technical_context(symbol, market) or f"未能獲取 {market}:{symbol} 的技術面資料。"
        if name == "get_stock_suggestions":
            symbol, market = arguments.get("symbol", ""), arguments.get("market", "CN")
            return build_stock_context(db, symbol, market) or f"暫無 {market}:{symbol} 的 AI 建議。"
        if name == "get_watchlist":
            return build_watchlist_context(db)
        return f"未知工具: {name}"
    except Exception as exc:  # noqa: BLE001 - preserve the legacy user-facing error contract
        logger.error("工具執行失敗 %s: %s", name, exc)
        return f"工具執行出錯: {exc}"
