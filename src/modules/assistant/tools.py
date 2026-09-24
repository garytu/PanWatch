"""PanWatch business adapters for the framework-free PanAgent runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from pan_agent import (
    RunRequest,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from sqlalchemy.orm import Session

from src.modules.market.price_alert_service import (
    compact_alert_rule,
    create_alert_rule,
    delete_alert_rule,
    get_alert_rule,
    list_alert_rules,
    update_alert_rule,
)
from src.modules.portfolio import build_portfolio_service
from src.modules.strategy.strategy_engine import list_strategy_signals
from src.platform.marketdata.collectors.discovery_collector import (
    EastMoneyDiscoveryCollector,
)
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.marketdata_client import (
    get_market_data,
    md_news,
    md_quote_rows,
)
from src.platform.marketdata.models import MARKETS, MarketCode
from src.platform.marketdata.stock_list import search_stocks
from src.platform.persistence.models import Stock
from src.platform.runtime.config import Settings


def _symbol_and_market(arguments: dict[str, Any]) -> tuple[str, MarketCode] | None:
    """Validate the small symbol contract shared by all market tools."""
    symbol = str(arguments.get("symbol") or "").strip().upper()
    try:
        market = MarketCode(str(arguments.get("market") or "CN").strip().upper())
    except ValueError:
        return None
    return (symbol, market) if symbol else None


def _failure_for_symbol(arguments: dict[str, Any]) -> ToolResult:
    if not str(arguments.get("symbol") or "").strip():
        return ToolResult.failure(
            summary="請提供要查詢的股票程式碼。", error_code="symbol_required"
        )
    return ToolResult.failure(summary="不支援的市場程式碼。", error_code="market_invalid")


def _optional_market(arguments: dict[str, Any]) -> MarketCode | None:
    """Parse an optional market filter without forcing queries to one market."""
    raw = str(arguments.get("market") or "").strip().upper()
    if not raw:
        return None
    try:
        return MarketCode(raw)
    except ValueError:
        return None


def _alert_condition_summary(item: dict[str, Any]) -> str:
    direction = "≥" if item.get("direction") == "above" else "≤"
    target = item.get("target_price")
    if target is None:
        return "條件未知"
    return f"價格 {direction} {target:g}"


def _published_at(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _json_safe(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _market_argument(arguments: dict[str, Any]) -> MarketCode | None:
    raw = str(arguments.get("market") or "CN").strip().upper()
    try:
        return MarketCode(raw)
    except ValueError:
        return None


def _discovery_collector() -> EastMoneyDiscoveryCollector:
    return EastMoneyDiscoveryCollector(proxy=Settings().http_proxy.strip() or None)


def _hot_stock_payload(item: object) -> dict[str, object]:
    return {
        "symbol": str(getattr(item, "symbol", "") or ""),
        "market": str(getattr(item, "market", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "price": getattr(item, "price", None),
        "change_pct": getattr(item, "change_pct", None),
        "turnover": getattr(item, "turnover", None),
        "volume": getattr(item, "volume", None),
    }


def _hot_board_payload(item: object) -> dict[str, object]:
    return {
        "code": str(getattr(item, "code", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "change_pct": getattr(item, "change_pct", None),
        "change_amount": getattr(item, "change_amount", None),
        "turnover": getattr(item, "turnover", None),
    }


def _format_candidate_price(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _compact_research_candidate(item: dict[str, Any]) -> dict[str, Any]:
    entry_low = item.get("entry_low")
    entry_high = item.get("entry_high")
    if entry_low is not None or entry_high is not None:
        entry_range = f"{_format_candidate_price(entry_low) or '--'} ~ {_format_candidate_price(entry_high) or '--'}"
    else:
        entry_range = ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
    quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return {
        "symbol": str(item.get("stock_symbol") or ""),
        "market": str(item.get("stock_market") or "CN"),
        "name": str(item.get("stock_name") or item.get("stock_symbol") or ""),
        "score": item.get("rank_score", item.get("score")),
        "action": item.get("action_label") or item.get("action") or "觀望",
        "risk": item.get("risk_level_label") or item.get("risk_level") or "未知",
        "source": item.get("source_pool_label") or item.get("source_pool") or "未知",
        "signal": item.get("signal") or "",
        "reason": item.get("reason") or "",
        "entry_range": entry_range,
        "target_price": item.get("target_price"),
        "stop_loss": item.get("stop_loss"),
        "invalidation": item.get("invalidation") or "",
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
    }


def build_panwatch_tool_registry(session: Session) -> ToolRegistry:
    """Register the host-owned market and portfolio tools for an assistant run."""
    registry = ToolRegistry()
    portfolio_service = build_portfolio_service(session)

    async def get_portfolio(_request: RunRequest, _arguments: dict) -> ToolResult:
        summary = portfolio_service.build_assistant_summary() or "使用者暫無持倉。"
        return ToolResult.success(
            summary=summary,
            data={"has_positions": summary != "使用者暫無持倉。"},
            sources=[{"name": "PanWatch 持倉"}],
            observed_at=datetime.now(UTC),
        )

    async def find_research_candidates(_request: RunRequest, arguments: dict) -> ToolResult:
        market = str(arguments.get("market") or "").strip().upper()
        holding = str(arguments.get("holding") or "unheld").strip().lower()
        risk_level = str(arguments.get("risk_level") or "").strip().lower()
        try:
            min_score = float(arguments.get("min_score", 70))
            limit = int(arguments.get("limit", 5))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="機會篩選引數無效。",
                error_code="candidate_filter_invalid",
            )
        if (
            (market and market not in {"CN", "HK", "US"})
            or holding not in {"all", "held", "unheld"}
            or (risk_level and risk_level not in {"all", "low", "medium", "high"})
            or not 0 <= min_score <= 100
            or not 1 <= limit <= 10
        ):
            return ToolResult.failure(
                summary="機會篩選引數無效。",
                error_code="candidate_filter_invalid",
            )

        try:
            result = await asyncio.to_thread(
                list_strategy_signals,
                market=market,
                status="active",
                min_score=min_score,
                limit=limit,
                source_pool="all",
                holding=holding,
                risk_level=risk_level,
                include_payload=True,
            )
        except Exception:  # noqa: BLE001 - provider/database failures become controlled tool results
            return ToolResult.failure(
                summary="機會資料暫時不可用。",
                error_code="candidate_data_unavailable",
            )

        items = [_compact_research_candidate(item) for item in result.get("items", [])]
        names = "、".join(item["name"] for item in items[:3])
        summary = (
            f"找到 {len(items)} 個研究候選：{names}。"
            if items
            else "暫無符合條件的研究候選。"
        )
        return ToolResult.success(
            summary=summary,
            data={
                "snapshot_date": result.get("snapshot_date") or "",
                "count": len(items),
                "items": items,
            },
            sources=[{"name": "PanWatch 機會訊號"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_quote(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - provider failures become controlled tool results
            return ToolResult.failure(
                summary="行情資料暫時不可用。", error_code="quote_unavailable"
            )
        quote = next(
            (row for row in rows if str(row.get("symbol") or "") == symbol), None
        )
        if quote is None:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的行情。",
                error_code="quote_unavailable",
            )
        data = {
            key: quote.get(key)
            for key in (
                "symbol",
                "name",
                "market",
                "current_price",
                "change_pct",
                "change_amount",
                "prev_close",
                "open_price",
                "high_price",
                "low_price",
                "volume",
                "turnover",
                "turnover_rate",
                "pe_ratio",
                "total_market_value",
                "circulating_market_value",
            )
        }
        name = data.get("name") or symbol
        return ToolResult.success(
            summary=(
                f"{name}（{market.value}:{symbol}）最新價 {data.get('current_price')}，"
                f"漲跌幅 {data.get('change_pct')}%。"
            ),
            data=data,
            sources=[{"name": "PanWatch 行情資料"}],
            observed_at=datetime.now(UTC),
        )

    async def get_kline_summary(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            summary = await asyncio.to_thread(
                KlineCollector(market).get_kline_summary, symbol
            )
        except Exception:  # noqa: BLE001 - source issues must not abort an agent run
            return ToolResult.failure(
                summary="K 線資料暫時不可用。", error_code="kline_unavailable"
            )
        if not isinstance(summary, dict) or not summary:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的 K 線摘要。",
                error_code="kline_unavailable",
            )
        return ToolResult.success(
            summary=f"{market.value}:{symbol} 的 K 線摘要已就緒：{summary}",
            data=summary,
            sources=[{"name": "PanWatch K 線資料"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_news(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            limit = max(1, min(int(arguments.get("limit") or 5), 10))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="新聞條數必須是數字。", error_code="limit_invalid"
            )
        try:
            articles = await asyncio.to_thread(
                md_news, [symbol], since_hours=168, names=None
            )
        except Exception:  # noqa: BLE001 - data-source failures stay within the tool result
            return ToolResult.failure(
                summary="新聞資料暫時不可用。", error_code="news_unavailable"
            )
        items = [
            {
                "title": str(getattr(article, "title", "") or ""),
                "source": str(getattr(article, "source", "") or ""),
                "published_at": _published_at(getattr(article, "publish_time", None)),
                "url": str(getattr(article, "url", "") or ""),
                "importance": int(getattr(article, "importance", 0) or 0),
            }
            for article in articles[:limit]
        ]
        return ToolResult.success(
            summary=f"{market.value}:{symbol} 近 7 天相關新聞 {len(items)} 條。",
            data={"symbol": symbol, "market": market.value, "items": items},
            sources=[{"name": "PanWatch 新聞資料"}],
            observed_at=datetime.now(UTC),
        )

    async def search_stocks_tool(_request: RunRequest, arguments: dict) -> ToolResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return ToolResult.failure(
                summary="請提供股票程式碼或名稱。", error_code="search_query_required"
            )
        raw_market = str(arguments.get("market") or "").strip().upper()
        if raw_market:
            try:
                market = MarketCode(raw_market)
            except ValueError:
                return ToolResult.failure(
                    summary="不支援的市場程式碼。", error_code="market_invalid"
                )
            market_value = market.value
        else:
            market_value = ""
        try:
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="搜尋條數必須是數字。", error_code="limit_invalid"
            )
        try:
            items = await asyncio.to_thread(search_stocks, query, market_value, limit)
        except Exception:  # noqa: BLE001 - search providers become controlled results
            return ToolResult.failure(
                summary="股票搜尋暫時不可用。", error_code="stock_search_unavailable"
            )
        data = [
            {
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or ""),
                "market": str(item.get("market") or ""),
            }
            for item in items
        ]
        return ToolResult.success(
            summary=(f"找到 {len(data)} 個股票標的。" if data else "沒有找到匹配的股票標的。"),
            data={"query": query, "count": len(data), "items": data},
            sources=[{"name": "PanWatch 股票清單"}],
            observed_at=datetime.now(UTC),
        )

    async def get_market_status(_request: RunRequest, _arguments: dict) -> ToolResult:
        markets = []
        for code, definition in MARKETS.items():
            try:
                is_trading = definition.is_trading_time()
            except Exception:  # noqa: BLE001 - calendar failures stay in the result
                is_trading = None
            sessions = [
                f"{item.start.strftime('%H:%M')}-{item.end.strftime('%H:%M')}"
                for item in definition.sessions
            ]
            markets.append(
                {
                    "market": code.value,
                    "name": definition.name,
                    "timezone": definition.timezone,
                    "status": (
                        "trading"
                        if is_trading is True
                        else "closed"
                        if is_trading is False
                        else "unknown"
                    ),
                    "is_trading": is_trading,
                    "sessions": sessions,
                }
            )
        return ToolResult.success(
            summary="；".join(
                f"{item['name']}"
                f"{'交易中' if item['is_trading'] is True else '已休市' if item['is_trading'] is False else '狀態未知'}"
                for item in markets
            ),
            data={"markets": markets},
            sources=[{"name": "PanWatch 市場日曆"}],
            observed_at=datetime.now(UTC),
        )

    async def get_hot_stocks(_request: RunRequest, arguments: dict) -> ToolResult:
        market = _market_argument(arguments)
        if market is None:
            return ToolResult.failure(
                summary="不支援的市場程式碼。", error_code="market_invalid"
            )
        mode = str(arguments.get("mode") or "turnover").strip().lower()
        if mode not in {"turnover", "gainers"}:
            return ToolResult.failure(
                summary="熱門股票排序只能是 turnover 或 gainers。",
                error_code="discovery_mode_invalid",
            )
        try:
            limit = max(1, min(int(arguments.get("limit") or 10), 30))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="熱門股票條數必須是數字。", error_code="limit_invalid"
            )
        try:
            items = await _discovery_collector().fetch_hot_stocks(
                market=market.value, mode=mode, limit=limit
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="熱門股票資料暫時不可用。", error_code="hot_stocks_unavailable"
            )
        data = [_hot_stock_payload(item) for item in items]
        return ToolResult.success(
            summary=f"找到 {len(data)} 個熱門股票。",
            data={"market": market.value, "mode": mode, "count": len(data), "items": data},
            sources=[{"name": "PanWatch 熱門股票"}],
            observed_at=datetime.now(UTC),
        )

    async def get_hot_boards(_request: RunRequest, arguments: dict) -> ToolResult:
        market = _market_argument(arguments)
        if market is None:
            return ToolResult.failure(
                summary="不支援的市場程式碼。", error_code="market_invalid"
            )
        mode = str(arguments.get("mode") or "gainers").strip().lower()
        if mode not in {"gainers", "turnover", "hot"}:
            return ToolResult.failure(
                summary="熱門板塊排序只能是 gainers、turnover 或 hot。",
                error_code="discovery_mode_invalid",
            )
        try:
            limit = max(1, min(int(arguments.get("limit") or 10), 20))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="熱門板塊條數必須是數字。", error_code="limit_invalid"
            )
        try:
            items = await _discovery_collector().fetch_hot_boards(
                market=market.value, mode=mode, limit=limit
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="熱門板塊資料暫時不可用。", error_code="hot_boards_unavailable"
            )
        data = [_hot_board_payload(item) for item in items]
        return ToolResult.success(
            summary=f"找到 {len(data)} 個熱門板塊。",
            data={"market": market.value, "mode": mode, "count": len(data), "items": data},
            sources=[{"name": "PanWatch 熱門板塊"}],
            observed_at=datetime.now(UTC),
        )

    async def get_board_stocks(_request: RunRequest, arguments: dict) -> ToolResult:
        board_code = str(arguments.get("board_code") or "").strip()
        if not board_code:
            return ToolResult.failure(
                summary="請提供板塊程式碼。", error_code="board_code_required"
            )
        mode = str(arguments.get("mode") or "gainers").strip().lower()
        if mode not in {"gainers", "turnover", "hot"}:
            return ToolResult.failure(
                summary="板塊股票排序只能是 gainers、turnover 或 hot。",
                error_code="discovery_mode_invalid",
            )
        try:
            limit = max(1, min(int(arguments.get("limit") or 10), 50))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="板塊股票條數必須是數字。", error_code="limit_invalid"
            )
        try:
            items = await _discovery_collector().fetch_board_stocks(
                board_code=board_code, mode=mode, limit=limit
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="板塊成分股資料暫時不可用。", error_code="board_stocks_unavailable"
            )
        data = [_hot_stock_payload(item) for item in items]
        return ToolResult.success(
            summary=f"找到 {len(data)} 個板塊成分股。",
            data={"board_code": board_code, "mode": mode, "count": len(data), "items": data},
            sources=[{"name": "PanWatch 板塊成分股"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_fundamentals(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            items = await asyncio.to_thread(
                lambda: get_market_data().fundamentals([symbol], market=market.value)
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="基本面資料暫時不可用。", error_code="fundamentals_unavailable"
            )
        if not items:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的基本面資料。",
                error_code="fundamentals_unavailable",
            )
        data = _json_safe(items[0])
        return ToolResult.success(
            summary=f"已獲取 {market.value}:{symbol} 的基本面摘要。",
            data=data,
            sources=[{"name": "PanWatch 基本面資料"}],
            observed_at=datetime.now(UTC),
        )

    async def get_capital_flow(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            item = await asyncio.to_thread(
                lambda: get_market_data().capital_flow(symbol, market=market.value)
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="資金流向資料暫時不可用。", error_code="capital_flow_unavailable"
            )
        if item is None:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的資金流向資料。",
                error_code="capital_flow_unavailable",
            )
        return ToolResult.success(
            summary=f"已獲取 {market.value}:{symbol} 的資金流向摘要。",
            data=_json_safe(item),
            sources=[{"name": "PanWatch 資金流向"}],
            observed_at=datetime.now(UTC),
        )

    async def get_dragon_tiger(_request: RunRequest, arguments: dict) -> ToolResult:
        trade_date = str(arguments.get("date") or "").strip()
        if not trade_date:
            return ToolResult.failure(
                summary="請提供龍虎榜日期，格式為 YYYY-MM-DD。",
                error_code="trade_date_required",
            )
        try:
            datetime.strptime(trade_date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return ToolResult.failure(
                summary="龍虎榜日期格式必須是 YYYY-MM-DD。",
                error_code="trade_date_invalid",
            )
        market = _market_argument(arguments)
        if market is None:
            return ToolResult.failure(
                summary="不支援的市場程式碼。", error_code="market_invalid"
            )
        try:
            items = await asyncio.to_thread(
                lambda: get_market_data().dragon_tiger(
                    date=trade_date, market=market.value
                )
            )
        except Exception:  # noqa: BLE001 - provider failures become controlled results
            return ToolResult.failure(
                summary="龍虎榜資料暫時不可用。", error_code="dragon_tiger_unavailable"
            )
        data = [_json_safe(item) for item in items]
        return ToolResult.success(
            summary=f"{trade_date} 找到 {len(data)} 條龍虎榜記錄。",
            data={"market": market.value, "date": trade_date, "count": len(data), "items": data},
            sources=[{"name": "PanWatch 龍虎榜"}],
            observed_at=datetime.now(UTC),
        )

    async def _find_or_register_stock(
        symbol: str, market: MarketCode
    ) -> tuple[Stock | None, bool]:
        """Resolve a stock id for write tools without requiring watchlist setup.

        Price-alert rules reference the local ``stocks`` table, while research
        tools can operate on any symbol returned by the market-data providers.
        A verified quote is enough to create the lightweight stock directory
        record; an unverified symbol remains a controlled ``stock_not_found``
        result and never produces a dangling alert rule.
        """
        stock = (
            session.query(Stock)
            .filter(Stock.symbol == symbol, Stock.market == market.value)
            .first()
        )
        if stock is not None:
            return stock, False

        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - quote failures become a controlled write failure
            return None, False

        def matches(row: dict[str, Any]) -> bool:
            row_symbol = str(row.get("symbol") or "").strip().upper()
            if market is MarketCode.HK and row_symbol.isdigit():
                row_symbol = row_symbol.zfill(5)
            return row_symbol == symbol

        quote = next((row for row in rows if matches(row)), None)
        if quote is None:
            return None, False

        stock = Stock(
            symbol=symbol,
            name=str(quote.get("name") or symbol).strip() or symbol,
            market=market.value,
        )
        session.add(stock)
        # 規則透過外部索引鍵引用新登記的股票；先 flush 獲取主鍵，仍由下方
        # 的單次 commit 保證股票目錄和提醒規則一起成功或一起回滾。
        session.flush()
        return stock, True

    async def create_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        direction = str(arguments.get("direction") or "").strip().lower()
        if direction not in {"above", "below"}:
            return ToolResult.failure(
                summary="提醒方向只能是 above 或 below。",
                error_code="direction_invalid",
            )
        try:
            target_price = float(arguments.get("target_price"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="提醒價格必須是大於零的數字。",
                error_code="target_price_invalid",
            )
        if target_price <= 0:
            return ToolResult.failure(
                summary="提醒價格必須大於零。", error_code="target_price_invalid"
            )
        try:
            cooldown_minutes = max(0, int(arguments.get("cooldown_minutes") or 30))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="冷卻時間必須是非負整數。", error_code="cooldown_invalid"
            )

        stock, stock_registered = await _find_or_register_stock(symbol, market)
        if stock is None:
            return ToolResult.failure(
                summary=f"PanWatch 股票庫中未找到 {market.value}:{symbol}，未建立提醒。",
                error_code="stock_not_found",
            )
        direction_label = "≥" if direction == "above" else "≤"
        display_price = f"{target_price:g}"
        name = (
            str(arguments.get("name") or "").strip()
            or f"{stock.name} 價格 {direction_label} {display_price}"
        )
        rule = create_alert_rule(
            session,
            stock_id=stock.id,
            name=name,
            enabled=True,
            condition_group={
                "op": "and",
                "items": [
                    {
                        "type": "price",
                        "op": ">=" if direction == "above" else "<=",
                        "value": target_price,
                    }
                ],
            },
            market_hours_mode="trading_only",
            cooldown_minutes=cooldown_minutes,
            max_triggers_per_day=3,
            repeat_mode="repeat",
            notify_channel_ids=[],
        )
        return ToolResult.success(
            summary=(
                f"已為 {stock.name}（{market.value}:{symbol}）建立價格 {direction_label} {display_price} "
                f"的盤中提醒，冷卻 {cooldown_minutes} 分鐘。"
            ),
            data={
                "rule_id": rule.id,
                "symbol": symbol,
                "market": market.value,
                "direction": direction,
                "target_price": target_price,
                "stock_registered": stock_registered,
            },
            sources=[{"name": "PanWatch 價格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def get_price_alerts(_request: RunRequest, arguments: dict) -> ToolResult:
        """Return compact alert facts so the model can reference a rule ID."""
        symbol = str(arguments.get("symbol") or "").strip().upper() or None
        market = _optional_market(arguments)
        if arguments.get("market") and market is None:
            return ToolResult.failure(
                summary="不支援的市場程式碼。", error_code="market_invalid"
            )
        if symbol and market is MarketCode.HK and symbol.isdigit():
            symbol = symbol.zfill(5)
        try:
            limit = max(1, min(int(arguments.get("limit") or 20), 50))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="查詢條數必須是數字。", error_code="limit_invalid"
            )
        enabled = arguments.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return ToolResult.failure(
                summary="enabled 必須是布林值。", error_code="enabled_invalid"
            )

        rows = list_alert_rules(
            session,
            symbol=symbol,
            market=market.value if market else None,
            enabled=enabled,
            limit=limit,
        )
        items = [compact_alert_rule(row) for row in rows]
        if not items:
            return ToolResult.success(
                summary="沒有找到符合條件的價格提醒。",
                data={"count": 0, "items": []},
                sources=[{"name": "PanWatch 價格提醒"}],
                observed_at=datetime.now(UTC),
            )
        summary = "；".join(
            f"#{item['rule_id']} {item['stock_name'] or item['symbol']}（{item['market']}:{item['symbol']}，"
            f"{_alert_condition_summary(item)}，{'啟用' if item['enabled'] else '停用'}）"
            for item in items[:5]
        )
        if len(items) > 5:
            summary += f"；另有 {len(items) - 5} 條"
        return ToolResult.success(
            summary=f"找到 {len(items)} 條價格提醒：{summary}",
            data={"count": len(items), "items": items},
            sources=[{"name": "PanWatch 價格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def update_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Update one rule after the runtime's human approval gate."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="請提供有效的提醒 ID。", error_code="rule_id_invalid"
            )
        updates = {
            key: arguments[key]
            for key in (
                "name",
                "enabled",
                "direction",
                "target_price",
                "cooldown_minutes",
                "max_triggers_per_day",
                "repeat_mode",
                "market_hours_mode",
                "expire_at",
            )
            if key in arguments
        }
        try:
            rule = update_alert_rule(session, rule_id, updates)
        except LookupError:
            return ToolResult.failure(
                summary=f"未找到價格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        except ValueError as exc:
            return ToolResult.failure(
                summary=str(exc), error_code="price_alert_invalid"
            )
        item = compact_alert_rule(rule)
        return ToolResult.success(
            summary=(
                f"已更新價格提醒 #{rule_id}：{item['stock_name'] or item['symbol']}，"
                f"{_alert_condition_summary(item)}，{'啟用' if item['enabled'] else '停用'}。"
            ),
            data=item,
            sources=[{"name": "PanWatch 價格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def delete_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Delete one rule and its hit history after human approval."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="請提供有效的提醒 ID。", error_code="rule_id_invalid"
            )
        rule = get_alert_rule(session, rule_id)
        if rule is None:
            return ToolResult.failure(
                summary=f"未找到價格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        item = compact_alert_rule(rule)
        try:
            delete_alert_rule(session, rule_id)
        except LookupError:
            return ToolResult.failure(
                summary=f"未找到價格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        return ToolResult.success(
            summary=f"已刪除價格提醒 #{rule_id}：{item['stock_name'] or item['symbol']}。",
            data={"rule_id": rule_id, "deleted": True},
            sources=[{"name": "PanWatch 價格提醒"}],
            observed_at=datetime.now(UTC),
        )

    registry.register(
        ToolSpec(
            name="get_portfolio",
            title="查詢持倉",
            description="查詢使用者的實盤和模擬交易持倉摘要。",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_portfolio,
    )
    registry.register(
        ToolSpec(
            name="get_stock_quote",
            title="查詢即時行情",
            description="查詢一隻股票的最新價、漲跌幅和日內交易資料。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票程式碼，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市場程式碼",
                    },
                },
            },
        ),
        get_stock_quote,
    )
    registry.register(
        ToolSpec(
            name="find_research_candidates",
            title="發現研究候選",
            description="查詢 PanWatch 最新機會訊號，返回適合進一步研究的候選標的及其評分、風險和入場計劃。只讀，不會重新整理策略或執行交易。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "description": "可選市場程式碼；不填表示全部市場",
                    },
                    "holding": {
                        "type": "string",
                        "enum": ["all", "held", "unheld"],
                        "default": "unheld",
                        "description": "持倉過濾；預設只看未持倉標的",
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["all", "low", "medium", "high"],
                        "default": "all",
                        "description": "可選風險等級過濾",
                    },
                    "min_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "default": 70,
                        "description": "最低機會分數",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                        "description": "最多返回候選數量",
                    },
                },
            },
        ),
        find_research_candidates,
    )
    registry.register(
        ToolSpec(
            name="get_kline_summary",
            title="分析 K 線走勢",
            description="獲取一隻股票的均線、動量和近期 K 線指標摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票程式碼，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市場程式碼",
                    },
                },
            },
        ),
        get_kline_summary,
    )
    registry.register(
        ToolSpec(
            name="get_stock_news",
            title="檢索股票新聞",
            description="檢索一隻股票最近七天的相關新聞並返回精簡摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票程式碼，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市場程式碼",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
            },
        ),
        get_stock_news,
    )
    registry.register(
        ToolSpec(
            name="search_stocks",
            title="搜尋股票標的",
            description="按股票程式碼或名稱搜尋 PanWatch 股票清單，用於確認標的程式碼和市場。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "股票程式碼或名稱"},
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "description": "可選市場程式碼；不填表示全部市場",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                    },
                },
            },
        ),
        search_stocks_tool,
    )
    registry.register(
        ToolSpec(
            name="get_market_status",
            title="查詢市場狀態",
            description="查詢 A 股、港股和美股當前是否處於交易時段及交易時間安排。",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_market_status,
    )
    registry.register(
        ToolSpec(
            name="get_hot_stocks",
            title="查詢熱門股票",
            description="按成交額或漲幅查詢指定市場的熱門股票榜單。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "default": "CN",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["turnover", "gainers"],
                        "default": "turnover",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 30,
                        "default": 10,
                    },
                },
            },
        ),
        get_hot_stocks,
    )
    registry.register(
        ToolSpec(
            name="get_hot_boards",
            title="查詢熱門板塊",
            description="按漲幅、成交額或熱度查詢指定市場的熱門板塊和主題。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "default": "CN",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["gainers", "turnover", "hot"],
                        "default": "gainers",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 10,
                    },
                },
            },
        ),
        get_hot_boards,
    )
    registry.register(
        ToolSpec(
            name="get_board_stocks",
            title="查詢板塊成分股",
            description="查詢指定板塊中按漲幅、成交額或熱度排序的成分股。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["board_code"],
                "properties": {
                    "board_code": {"type": "string", "description": "板塊程式碼"},
                    "mode": {
                        "type": "string",
                        "enum": ["gainers", "turnover", "hot"],
                        "default": "gainers",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                },
            },
        ),
        get_board_stocks,
    )
    registry.register(
        ToolSpec(
            name="get_stock_fundamentals",
            title="查詢股票基本面",
            description="查詢一隻股票的估值、盈利、成長和財報期等基本面摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string", "description": "股票程式碼，例如 600519"},
                    "market": {"type": "string", "default": "CN"},
                },
            },
        ),
        get_stock_fundamentals,
    )
    registry.register(
        ToolSpec(
            name="get_capital_flow",
            title="查詢資金流向",
            description="查詢一隻股票的主力、超大單和大單等資金流向摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {"type": "string", "description": "股票程式碼，例如 600519"},
                    "market": {"type": "string", "default": "CN"},
                },
            },
        ),
        get_capital_flow,
    )
    registry.register(
        ToolSpec(
            name="get_dragon_tiger",
            title="查詢龍虎榜",
            description="查詢指定交易日的龍虎榜上榜股票、上榜原因和買賣金額。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["date"],
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "交易日期，格式 YYYY-MM-DD",
                    },
                    "market": {"type": "string", "default": "CN"},
                },
            },
        ),
        get_dragon_tiger,
    )
    registry.register(
        ToolSpec(
            name="get_price_alerts",
            title="查詢價格提醒",
            description="查詢使用者已建立的價格提醒，返回提醒 ID、標的、條件和啟用狀態。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "可選股票程式碼；不填則查詢所有標的",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "description": "可選市場程式碼",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "可選，僅返回啟用或停用的提醒",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                },
            },
        ),
        get_price_alerts,
    )
    registry.register(
        ToolSpec(
            name="update_price_alert",
            title="修改價格提醒",
            description="修改一條價格提醒的名稱、目標價、方向或啟用狀態，需要使用者批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "查詢價格提醒得到的提醒 ID",
                    },
                    "name": {"type": "string", "description": "新的提醒名稱"},
                    "enabled": {"type": "boolean", "description": "是否啟用"},
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "價格觸發方向",
                    },
                    "target_price": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "新的目標價格",
                    },
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "觸發後的冷卻分鐘數",
                    },
                    "max_triggers_per_day": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "每日最大觸發次數，0 表示不限制",
                    },
                    "repeat_mode": {
                        "type": "string",
                        "enum": ["once", "repeat"],
                    },
                    "market_hours_mode": {
                        "type": "string",
                        "enum": ["trading_only", "always"],
                    },
                    "expire_at": {
                        "type": ["string", "null"],
                        "description": "ISO-8601 到期時間；傳 null 清除到期時間",
                    },
                },
            },
        ),
        update_price_alert,
    )
    registry.register(
        ToolSpec(
            name="delete_price_alert",
            title="刪除價格提醒",
            description="刪除一條價格提醒及其歷史命中記錄，需要使用者批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "查詢價格提醒得到的提醒 ID",
                    }
                },
            },
        ),
        delete_price_alert,
    )
    registry.register(
        ToolSpec(
            name="create_price_alert",
            title="建立價格提醒",
            description="為已收錄的股票建立盤中價格提醒，需要使用者批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["symbol", "direction", "target_price"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票程式碼，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市場程式碼",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "價格向上或向下觸及目標價",
                    },
                    "target_price": {"type": "number", "exclusiveMinimum": 0},
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                    },
                    "name": {"type": "string", "description": "可選的提醒名稱"},
                },
            },
        ),
        create_price_alert,
    )
    for name in (
        "find_research_candidates",
        "get_kline_summary",
        "get_hot_stocks",
        "get_hot_boards",
        "get_board_stocks",
        "get_stock_fundamentals",
        "get_capital_flow",
        "get_dragon_tiger",
        "update_price_alert",
        "delete_price_alert",
        "create_price_alert",
    ):
        registry.set_exposure(name, ToolExposure.DEFERRED)
    return registry
