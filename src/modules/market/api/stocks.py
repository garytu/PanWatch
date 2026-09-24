import asyncio
import logging
import threading
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    Stock,
    StockAgent,
    AgentConfig,
    Position,
    PriceAlertRule,
    PriceAlertHit,
)
from src.platform.marketdata.stock_list import search_stocks, refresh_stock_list
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.models import MarketCode, MARKETS
from src.modules.automation.agent_catalog import AGENT_KIND_WORKFLOW, infer_agent_kind

logger = logging.getLogger(__name__)
router = APIRouter()


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: str = "CN"


class StockUpdate(BaseModel):
    name: str | None = None


class StockAgentInfo(BaseModel):
    agent_name: str
    display_name: str = ""
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockResponse(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    sort_order: int
    agents: list[StockAgentInfo] = []

    class Config:
        from_attributes = True


class StockAgentItem(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockAgentUpdate(BaseModel):
    agents: list[StockAgentItem]


class StockReorderItem(BaseModel):
    id: int
    sort_order: int


class StockReorderRequest(BaseModel):
    items: list[StockReorderItem]


def _agent_display_names(db: Session, stocks: list[Stock]) -> dict[str, str]:
    agent_names = {
        sa.agent_name
        for stock in stocks
        for sa in stock.agents
        if infer_agent_kind(sa.agent_name) == AGENT_KIND_WORKFLOW
    }
    if not agent_names:
        return {}

    rows = (
        db.query(AgentConfig.name, AgentConfig.display_name)
        .filter(AgentConfig.name.in_(agent_names))
        .all()
    )
    return {name: display_name or name for name, display_name in rows}


def _stock_to_response(stock: Stock, agent_display_names: dict[str, str] | None = None) -> dict:
    display_names = agent_display_names or {}
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "sort_order": stock.sort_order or 0,
        "agents": [
            {
                "agent_name": sa.agent_name,
                "display_name": display_names.get(sa.agent_name, sa.agent_name),
                "schedule": sa.schedule or "",
                "ai_model_id": sa.ai_model_id,
                "notify_channel_ids": sa.notify_channel_ids or [],
            }
            for sa in stock.agents
            if infer_agent_kind(sa.agent_name) == AGENT_KIND_WORKFLOW
        ],
    }


@router.get("/markets/status")
def get_market_status():
    """獲取各市場的交易狀態"""
    from datetime import datetime

    result = []
    for market_code, market_def in MARKETS.items():
        try:
            now = datetime.now(market_def.get_tz())
            is_trading = market_def.is_trading_time()

            # 獲取交易時段描述
            sessions_desc = []
            for session in market_def.sessions:
                sessions_desc.append(f"{session.start.strftime('%H:%M')}-{session.end.strftime('%H:%M')}")

            # 判斷狀態
            weekday = now.weekday()
            current_time = now.time()

            if weekday >= 5:
                status = "closed"
                status_text = "休市（週末）"
            elif is_trading:
                status = "trading"
                status_text = "交易中"
            else:
                # 判斷是盤前還是盤後
                first_session = market_def.sessions[0]
                last_session = market_def.sessions[-1]
                if current_time < first_session.start:
                    status = "pre_market"
                    status_text = "盤前"
                elif current_time > last_session.end:
                    status = "after_hours"
                    status_text = "已收盤"
                else:
                    status = "break"
                    status_text = "午間休市"

            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": status,
                "status_text": status_text,
                "is_trading": is_trading,
                "sessions": sessions_desc,
                "local_time": now.strftime("%H:%M"),
                "timezone": market_def.timezone,
            })
        except Exception as e:
            # 單個市場獲取失敗不影響其他市場
            logger.error(f"獲取 {market_code.value} 市場狀態失敗: {e}")
            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": "unknown",
                "status_text": "未知",
                "is_trading": False,
                "sessions": [],
                "local_time": "--:--",
                "timezone": market_def.timezone,
                "error": str(e),
            })

    return result


@router.get("/search")
def search(q: str = Query("", min_length=1), market: str = Query("")):
    """模糊搜尋股票(程式碼/名稱)"""
    return search_stocks(q, market)


@router.post("/refresh-list")
def refresh_list():
    """重新整理股票列表快取"""
    stocks = refresh_stock_list()
    return {"count": len(stocks)}


@router.get("", response_model=list[StockResponse])
def list_stocks(db: Session = Depends(get_db)):
    stocks = db.query(Stock).order_by(Stock.sort_order.asc(), Stock.id.asc()).all()
    agent_display_names = _agent_display_names(db, stocks)
    return [_stock_to_response(s, agent_display_names) for s in stocks]


@router.get("/quotes")
def get_quotes(db: Session = Depends(get_db)):
    """獲取所有自選股的即時行情"""
    stocks = db.query(Stock).all()
    if not stocks:
        return {}

    # 按市場分組
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            MarketCode(market)  # 校驗市場合法
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]   # 原始碼,md 內部按市場格式化
        try:
            items = md_quote_rows(symbols, market)
            for item in items:
                quotes[item["symbol"]] = {
                    "current_price": item["current_price"],
                    "change_pct": item["change_pct"],
                    "change_amount": item["change_amount"],
                    "prev_close": item["prev_close"],
                }
        except Exception as e:
            logger.error(f"獲取 {market} 行情失敗: {e}")

    return quotes


@router.post("", response_model=StockResponse)
def create_stock(stock: StockCreate, db: Session = Depends(get_db)):
    existing = db.query(Stock).filter(
        Stock.symbol == stock.symbol, Stock.market == stock.market
    ).first()
    if existing:
        raise HTTPException(400, f"股票 {stock.symbol} 已存在")

    max_order = db.query(func.max(Stock.sort_order)).scalar() or 0
    db_stock = Stock(**stock.model_dump(), sort_order=int(max_order) + 1)
    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.put("/reorder")
def reorder_stocks(body: StockReorderRequest, db: Session = Depends(get_db)):
    if not body.items:
        return {"updated": 0}
    ids = [int(x.id) for x in body.items]
    rows = db.query(Stock).filter(Stock.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in body.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


@router.put("/{stock_id}", response_model=StockResponse)
def update_stock(stock_id: int, stock: StockUpdate, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for key, value in stock.model_dump(exclude_unset=True).items():
        setattr(db_stock, key, value)

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.delete("/{stock_id}")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    # 刪除股票前，要求先清理持倉，避免誤刪資產資料。
    has_position = db.query(Position.id).filter(Position.stock_id == stock_id).first()
    if has_position:
        raise HTTPException(400, "該股票存在持倉，請先刪除持倉後再刪除股票")

    # SQLite 預設可能不啟用 FK 級聯，手動清理提醒資料避免孤兒記錄。
    rule_ids = [
        row[0]
        for row in db.query(PriceAlertRule.id).filter(
            PriceAlertRule.stock_id == stock_id
        ).all()
    ]
    if rule_ids:
        db.query(PriceAlertHit).filter(PriceAlertHit.rule_id.in_(rule_ids)).delete(
            synchronize_session=False
        )
    db.query(PriceAlertHit).filter(PriceAlertHit.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(PriceAlertRule).filter(PriceAlertRule.stock_id == stock_id).delete(
        synchronize_session=False
    )
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete(
        synchronize_session=False
    )

    db.delete(db_stock)
    db.commit()
    return {"ok": True}


@router.put("/{stock_id}/agents", response_model=StockResponse)
def update_stock_agents(stock_id: int, body: StockAgentUpdate, db: Session = Depends(get_db)):
    """更新股票關聯的 Agent 列表（含排程配置和 AI/通知覆蓋）"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for item in body.agents:
        agent = db.query(AgentConfig).filter(AgentConfig.name == item.agent_name).first()
        if not agent:
            raise HTTPException(400, f"Agent {item.agent_name} 不存在")
        agent_kind = (agent.kind or "").strip() or infer_agent_kind(agent.name)
        if agent_kind != AGENT_KIND_WORKFLOW:
            raise HTTPException(400, f"Agent {item.agent_name} 為內部能力，不支援繫結到股票")

    # 清除舊關聯，重建
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete()
    for item in body.agents:
        db.add(StockAgent(
            stock_id=stock_id,
            agent_name=item.agent_name,
            schedule=item.schedule,
            ai_model_id=item.ai_model_id,
            notify_channel_ids=item.notify_channel_ids,
        ))

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock, _agent_display_names(db, [db_stock]))


@router.post("/{stock_id}/agents/{agent_name}/trigger")
async def trigger_stock_agent(
    stock_id: int,
    agent_name: str,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    allow_unbound: bool = False,
    wait: bool = False,
    force_refresh: bool = False,
    symbol: str = Query(""),
    market: str = Query("CN"),
    name: str = Query(""),
    db: Session = Depends(get_db),
):
    """手動觸發單隻股票 Agent。

    - 正常模式：傳有效 stock_id
    - 無繫結模式：stock_id<=0 且傳 symbol/market（需 allow_unbound=true）
    - 無繫結模式預設停用通知（僅生成建議）
    - 預設非同步執行（立即返回），傳 wait=true 可同步等待結果
    """
    sa = None
    trigger_stock = None
    suppress_notify = stock_id <= 0

    if stock_id > 0:
        db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not db_stock:
            raise HTTPException(404, "股票不存在")

        sa = db.query(StockAgent).filter(
            StockAgent.stock_id == stock_id, StockAgent.agent_name == agent_name
        ).first()
        if not sa and not allow_unbound:
            raise HTTPException(400, f"股票未關聯 Agent {agent_name}")
        if not sa and allow_unbound:
            # 允許無繫結觸發時，至少確保 Agent 存在。
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} 不存在")
        trigger_stock = db_stock
    else:
        symbol = (symbol or "").strip()
        if not symbol:
            raise HTTPException(400, "當 stock_id<=0 時，symbol 不能為空")
        if not allow_unbound:
            raise HTTPException(400, "當 stock_id<=0 時，需設定 allow_unbound=true")

        market = (market or "CN").strip().upper() or "CN"
        name = (name or "").strip() or symbol
        db_stock = db.query(Stock).filter(
            Stock.symbol == symbol, Stock.market == market
        ).first()
        if db_stock:
            sa = db.query(StockAgent).filter(
                StockAgent.stock_id == db_stock.id, StockAgent.agent_name == agent_name
            ).first()
            trigger_stock = db_stock
        else:
            # 不落庫：用於詳細資訊彈跳視窗未持倉且未關注股票的一次性分析。
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if not agent:
                raise HTTPException(400, f"Agent {agent_name} 不存在")
            trigger_stock = SimpleNamespace(
                id=0,
                symbol=symbol,
                name=name,
                market=market,
            )

    logger.info(
        f"手動觸發 Agent {agent_name} - {trigger_stock.name}({trigger_stock.symbol})"
    )

    from server import trigger_agent_for_stock
    import time as _time

    # 冪等性兜底:TradingAgents 單次 3-5 分鐘,前端誤操作/雙擊可能併發觸發同一標的。
    # 後端先查"該 symbol 是否有真正在跑的 TA 任務",有則返回現有 trace_id(不啟新任務)。
    # force_refresh=true 時跳過去重,允許使用者主動強制重跑(老任務自然終止,新 trace_id)。
    if agent_name == "tradingagents" and not force_refresh:
        from src.modules.automation import find_active_tradingagents_trace
        existing_trace = find_active_tradingagents_trace(db, trigger_stock.symbol)
        if existing_trace:
            logger.info(
                f"[trigger 冪等] {trigger_stock.symbol} 已有在跑任務 trace={existing_trace},"
                f"複用而非啟新任務"
            )
            return {
                "queued": False,
                "trace_id": existing_trace,
                "message": "已有正在執行的深度分析,返回現有任務進度",
                "deduplicated": True,
            }

    # 預生成 trace_id,返回給前端用於輪詢進度
    trace_id = f"man-{agent_name}-{trigger_stock.symbol}-{int(_time.time() * 1000)}"

    # 生命週期先落庫，確保後臺執行緒尚未寫出第一條進度日誌時，重新整理頁面仍能恢復任務。
    if agent_name == "tradingagents":
        try:
            from src.modules.automation.agent_runs import start_agent_run
            start_agent_run(
                agent_name=agent_name,
                trace_id=trace_id,
                trigger_source="manual",
            )
        except Exception as e:
            logger.warning(f"[TA] 寫 running 生命週期失敗,不影響主流程: {e}")

    # 立刻寫一條"任務已觸發"進度日誌,保證前端 polling 第一拍就能看到 running。
    # 否則 trigger_agent_for_stock 內部要先 await agent.collect()(美股拉 yfinance 資料
    # 可能 30s+),期間沒有任何 ta_progress 日誌 → 前端 progress 介面返回 not_found
    # → 60s grace 過後前端 reset 到 idle,看起來像"進度卡死自動退回"。
    if agent_name == "tradingagents":
        try:
            from src.platform.observability.log_context import log_context
            with log_context(
                trace_id=trace_id,
                agent_name="tradingagents",
                event="ta_progress",
                tags={"stage": "task_triggered", "action": "triggered"},
            ):
                logger.info(
                    f"[TA] 任務已觸發 - {trigger_stock.symbol} (trace={trace_id})"
                )
        except Exception as e:
            logger.warning(f"[TA] 寫觸發日誌失敗,不影響主流程: {e}")

    if not wait:
        # 非同步模式：後臺執行，立即返回
        sa_id = sa.id if sa else None

        def _runner():
            try:
                asyncio.run(trigger_agent_for_stock(
                    agent_name,
                    trigger_stock,
                    stock_agent_id=sa_id,
                    bypass_throttle=bypass_throttle,
                    bypass_market_hours=bypass_market_hours,
                    suppress_notify=suppress_notify,
                    trace_id=trace_id,
                    force_refresh=force_refresh,
                ))
                logger.info(f"Agent {agent_name} 後臺執行完成 - {trigger_stock.symbol}")
            except Exception:
                logger.exception(f"Agent {agent_name} 後臺執行失敗 - {trigger_stock.symbol}")

        t = threading.Thread(
            target=_runner,
            name=f"stock-trigger-{agent_name}-{trigger_stock.symbol}",
            daemon=True,
        )
        t.start()
        return {"queued": True, "trace_id": trace_id, "message": "已提交後臺執行"}

    # 同步模式：等待結果返回
    try:
        result = await trigger_agent_for_stock(
            agent_name,
            trigger_stock,
            stock_agent_id=sa.id if sa else None,
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
            suppress_notify=suppress_notify,
            trace_id=trace_id,
            force_refresh=force_refresh,
        )
        logger.info(f"Agent {agent_name} 執行完成 - {trigger_stock.symbol}")
        return {
            "result": result,
            "trace_id": trace_id,
            "code": int(result.get("code", 0)),
            "success": bool(result.get("success", True)),
            "message": result.get("message", "ok"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Agent {agent_name} 執行失敗 - {trigger_stock.symbol}: {e}")
        raise HTTPException(500, f"Agent 執行失敗: {e}")
