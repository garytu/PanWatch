"""帳戶和持倉管理 API"""
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from datetime import datetime, timedelta, timezone

from src.platform.persistence.database import get_db
from src.platform.persistence.models import Account, PriceAlertRule, Position, Stock
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.marketdata.models import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()

# 匯率快取
_hkd_rate_cache: dict = {"rate": 0.92, "ts": 0}  # 港幣預設匯率 0.92
_usd_rate_cache: dict = {"rate": 7.25, "ts": 0}  # 美元預設匯率 7.25
EXCHANGE_RATE_TTL = 3600  # 1 小時快取


def get_hkd_cny_rate() -> float:
    """獲取港幣兌人民幣匯率"""
    global _hkd_rate_cache

    # 檢查快取
    if time.time() - _hkd_rate_cache["ts"] < EXCHANGE_RATE_TTL:
        return _hkd_rate_cache["rate"]

    # 從新浪財經獲取匯率
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=fx_shkdcny",
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/"
            }
        )
        # 格式: var hq_str_fx_shkdcny="時間,匯率,..."
        text = resp.text
        if "=" in text and "," in text:
            data = text.split('"')[1]
            parts = data.split(",")
            if len(parts) > 1:
                rate = float(parts[1])
                _hkd_rate_cache = {"rate": rate, "ts": time.time()}
                logger.info(f"更新港幣匯率: {rate}")
                return rate
    except Exception as e:
        logger.warning(f"獲取港幣匯率失敗，使用快取: {e}")

    return _hkd_rate_cache["rate"]


def get_usd_cny_rate() -> float:
    """獲取美元兌人民幣匯率"""
    global _usd_rate_cache

    # 檢查快取
    if time.time() - _usd_rate_cache["ts"] < EXCHANGE_RATE_TTL:
        return _usd_rate_cache["rate"]

    # 從新浪財經獲取匯率
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=fx_susdcny",
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/"
            }
        )
        # 格式: var hq_str_fx_susdcny="時間,匯率,..."
        text = resp.text
        if "=" in text and "," in text:
            data = text.split('"')[1]
            parts = data.split(",")
            if len(parts) > 1:
                rate = float(parts[1])
                _usd_rate_cache = {"rate": rate, "ts": time.time()}
                logger.info(f"更新美元匯率: {rate}")
                return rate
    except Exception as e:
        logger.warning(f"獲取美元匯率失敗，使用快取: {e}")

    return _usd_rate_cache["rate"]


# ========== Pydantic Models ==========

class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    enabled: bool

    class Config:
        from_attributes = True


class PositionCreate(BaseModel):
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str | None = None  # short: 短線, swing: 波段, long: 長線


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    sort_order: int
    trading_style: str | None
    # 關聯資訊
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    class Config:
        from_attributes = True


class PositionReorderItem(BaseModel):
    id: int
    sort_order: int


class PositionReorderRequest(BaseModel):
    items: list[PositionReorderItem]


# ========== Account Endpoints ==========

@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(db: Session = Depends(get_db)):
    """獲取所有帳戶"""
    return db.query(Account).order_by(Account.id).all()


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """獲取單個帳戶"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "帳戶不存在")
    return account


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    """建立帳戶"""
    account = Account(name=data.name, available_funds=data.available_funds)
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"建立帳戶: {account.name}")
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    """更新帳戶"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "帳戶不存在")

    if data.name is not None:
        account.name = data.name
    if data.available_funds is not None:
        account.available_funds = data.available_funds
    if data.enabled is not None:
        account.enabled = data.enabled

    db.commit()
    db.refresh(account)
    logger.info(f"更新帳戶: {account.name}")
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """刪除帳戶（會同時刪除該帳戶的所有持倉）"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "帳戶不存在")

    # Read relationship-independent values before commit.  SQLAlchemy expires
    # and detaches deleted instances, so accessing ``account.name`` after the
    # commit may trigger a lazy load and raise DetachedInstanceError.
    account_name = account.name
    db.delete(account)
    db.commit()
    logger.info("刪除帳戶: %s", account_name)
    return {"success": True}


# ========== Position Endpoints ==========

@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    account_id: int | None = None,
    stock_id: int | None = None,
    db: Session = Depends(get_db)
):
    """獲取持倉列表，可按帳戶或股票篩選"""
    query = db.query(Position)
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)

    positions = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()).all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "sort_order": pos.sort_order or 0,
            "trading_style": pos.trading_style,
            "account_name": pos.account.name if pos.account else None,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
        })
    return result


@router.post("/positions", response_model=PositionResponse)
def create_position(data: PositionCreate, db: Session = Depends(get_db)):
    """建立持倉"""
    # 檢查帳戶和股票是否存在
    account = db.query(Account).filter(Account.id == data.account_id).first()
    if not account:
        raise HTTPException(400, "帳戶不存在")

    stock = db.query(Stock).filter(Stock.id == data.stock_id).first()
    if not stock:
        raise HTTPException(400, "股票不存在")

    # 檢查是否已存在該帳戶的該股票持倉
    existing = db.query(Position).filter(
        Position.account_id == data.account_id,
        Position.stock_id == data.stock_id,
    ).first()
    if existing:
        raise HTTPException(400, f"帳戶 {account.name} 已有 {stock.name} 的持倉，請編輯現有持倉")

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == data.account_id
    ).scalar() or 0

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        sort_order=int(max_order) + 1,
        trading_style=data.trading_style,
    )
    db.add(position)
    db.commit()
    db.refresh(position)

    logger.info(f"建立持倉: {account.name} - {stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db)):
    """更新持倉"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持倉不存在")

    if data.cost_price is not None:
        position.cost_price = data.cost_price
    if data.quantity is not None:
        position.quantity = data.quantity
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
    if data.trading_style is not None:
        # 空字串表示清空，設為 None
        position.trading_style = data.trading_style if data.trading_style else None

    db.commit()
    db.refresh(position)

    logger.info(f"更新持倉: {position.account.name} - {position.stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db)):
    """刪除持倉"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持倉不存在")

    # Capture lazy relationships before the row is deleted/committed.  The
    # deleted Position is no longer session-bound afterwards; logging its
    # relationships at that point can raise DetachedInstanceError.
    account_name = position.account.name if position.account else "未知帳戶"
    stock_name = position.stock.name if position.stock else "未知股票"
    db.delete(position)
    db.commit()
    logger.info("刪除持倉: %s - %s", account_name, stock_name)
    return {"success": True}


@router.put("/positions/reorder/batch")
def reorder_positions(data: PositionReorderRequest, db: Session = Depends(get_db)):
    """批次更新持倉排序"""
    if not data.items:
        return {"updated": 0}
    ids = [int(x.id) for x in data.items]
    rows = db.query(Position).filter(Position.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in data.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


# ========== Portfolio Summary ==========

@router.get("/portfolio/summary")
def get_portfolio_summary(
    account_id: int | None = None,
    include_quotes: bool = True,
    db: Session = Depends(get_db),
):
    """
    獲取持倉彙總資訊

    Args:
        account_id: 可選，指定帳戶ID。不指定則彙總所有帳戶

    Returns:
        accounts: 帳戶列表及各帳戶持倉明細
        total: 所有帳戶彙總
    """
    # 獲取帳戶
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.enabled == True).all()
    else:
        accounts = db.query(Account).filter(Account.enabled == True).all()

    if not accounts:
        return {
            "accounts": [],
            "total": {
                "total_market_value": 0,
                "total_cost": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "available_funds": 0,
                "total_assets": 0,
            }
        }

    # 獲取所有相關股票
    all_stock_ids = set()
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}

    # 獲取即時行情（可選）
    quotes = _fetch_quotes_for_stocks(stocks) if include_quotes else {}

    # 獲取匯率
    hkd_rate = get_hkd_cny_rate()
    usd_rate = get_usd_cny_rate()

    # 計算各帳戶持倉
    account_summaries = []
    grand_total_market_value = 0
    grand_total_cost = 0
    grand_available_funds = 0
    grand_daily_pnl = 0

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0
        acc_daily_pnl = 0

        positions_sorted = sorted(
            list(acc.positions or []),
            key=lambda p: (int(getattr(p, "sort_order", 0) or 0), int(p.id)),
        )
        for pos in positions_sorted:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            quote = quotes.get(stock.symbol)
            current_price = quote["current_price"] if quote else None
            change_pct = quote["change_pct"] if quote else None
            prev_close = quote.get("prev_close") if quote else None

            # 根據市場確定匯率
            is_foreign = stock.market in ("HK", "US")
            if stock.market == "HK":
                rate = hkd_rate
            elif stock.market == "US":
                rate = usd_rate
            else:
                rate = 1.0

            market_value = None
            market_value_cny = None
            pnl = None
            pnl_pct = None
            daily_pnl = None
            daily_pnl_pct = None

            if current_price is not None and prev_close and prev_close > 0:
                daily_pnl = (current_price - prev_close) * pos.quantity * rate
                daily_pnl_pct = (current_price - prev_close) / prev_close * 100
                acc_daily_pnl += daily_pnl

            cost = pos.cost_price * pos.quantity
            cost_cny = cost * rate  # 假設成本價也是原幣種
            acc_cost += cost_cny

            if current_price is not None:
                market_value = current_price * pos.quantity  # 原幣種市值
                market_value_cny = market_value * rate  # 人民幣市值
                pnl = market_value_cny - cost_cny
                pnl_pct = (pnl / cost_cny * 100) if cost_cny > 0 else 0

                acc_market_value += market_value_cny

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "sort_order": pos.sort_order or 0,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "current_price_cny": round(current_price * rate, 2) if current_price else None,
                "change_pct": change_pct,
                "market_value": round(market_value, 2) if market_value else None,
                "market_value_cny": round(market_value_cny, 2) if market_value_cny else None,
                "pnl": round(pnl, 2) if pnl else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                "daily_pnl": round(daily_pnl, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": round(daily_pnl_pct, 2) if daily_pnl_pct is not None else None,
                "exchange_rate": rate if is_foreign else None,
            })

        if include_quotes:
            acc_pnl = acc_market_value - acc_cost
            acc_pnl_pct = (acc_pnl / acc_cost * 100) if acc_cost > 0 else 0
            acc_total_assets = acc_market_value + acc.available_funds
        else:
            acc_pnl = 0
            acc_pnl_pct = 0
            acc_total_assets = acc.available_funds

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "total_cost": round(acc_cost, 2),
            "total_pnl": round(acc_pnl, 2),
            "total_pnl_pct": round(acc_pnl_pct, 2),
            "total_daily_pnl": round(acc_daily_pnl, 2),
            "total_assets": round(acc_total_assets, 2),
            "positions": positions_data,
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_cost
        grand_available_funds += acc.available_funds
        grand_daily_pnl += acc_daily_pnl

    if include_quotes:
        grand_pnl = grand_total_market_value - grand_total_cost
        grand_pnl_pct = (grand_pnl / grand_total_cost * 100) if grand_total_cost > 0 else 0
        grand_total_assets = grand_total_market_value + grand_available_funds
    else:
        grand_pnl = 0
        grand_pnl_pct = 0
        grand_total_assets = grand_available_funds

    # 構建 quotes 字典（用於前端股票列表顯示）
    quotes_dict = {}
    if include_quotes:
        for symbol, quote in quotes.items():
            quotes_dict[symbol] = {
                "current_price": quote.get("current_price"),
                "change_pct": quote.get("change_pct"),
            }

    return {
        "accounts": account_summaries,
        "total": {
            "total_market_value": round(grand_total_market_value, 2),
            "total_cost": round(grand_total_cost, 2),
            "total_pnl": round(grand_pnl, 2),
            "total_pnl_pct": round(grand_pnl_pct, 2),
            "total_daily_pnl": round(grand_daily_pnl, 2),
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2),
        },
        "exchange_rates": {
            "HKD_CNY": hkd_rate,
            "USD_CNY": usd_rate,
        },
        "quotes": quotes_dict,  # 可選：返回行情資料
    }


def _fetch_quotes_for_stocks(stocks: list[Stock]) -> dict:
    """獲取股票列表的即時行情"""
    if not stocks:
        return {}

    # 按市場分組
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [s.symbol for s in stock_list]
        try:
            items = md_quote_rows(symbols, market_code.value)
            for item in items:
                quotes[item["symbol"]] = item
        except Exception as e:
            logger.error(f"獲取 {market} 行情失敗: {e}")

    return quotes


# 組合基準/歸因結果快取:重建全持倉 NAV 很貴(逐只拉 K 線),按持倉指紋快取結果。
# 持倉變動即失效(指紋變);失敗/空結果不快取,避免把瞬時故障凍住 10 分鐘。
_PORTFOLIO_RESULT_CACHE = TTLCache(default_ttl_sec=600.0)


def _holdings_signature(db: Session) -> str:
    """啟用帳戶持倉的穩定指紋(stock_id + 合併後數量);僅查 DB,不拉行情/K 線。"""
    rows = (
        db.query(Position.stock_id, Position.quantity)
        .join(Account, Account.id == Position.account_id)
        .filter(Account.enabled == True)  # noqa: E712
        .all()
    )
    agg: dict[int, float] = {}
    for sid, qty in rows:
        agg[sid] = agg.get(sid, 0.0) + (qty or 0)
    return ";".join(f"{sid}:{agg[sid]:g}" for sid in sorted(agg))


def _gather_holdings(db: Session) -> list[dict]:
    """彙總所有啟用帳戶的真實持倉為統一列表(CNY 市值/未實現獲利 + fx),多帳戶同股合併。"""
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    stock_ids = {p.stock_id for acc in accounts for p in acc.positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    hkd, usd = get_hkd_cny_rate(), get_usd_cny_rate()

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for acc in accounts:
        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue
            rate = hkd if stock.market == "HK" else usd if stock.market == "US" else 1.0
            quote = quotes.get(stock.symbol)
            price = quote.get("current_price") if quote else None
            cost_cny = pos.cost_price * pos.quantity * rate
            mv_cny = (price * pos.quantity * rate) if price else cost_cny
            pnl_cny = (mv_cny - cost_cny) if price else 0.0
            key = (stock.market, stock.symbol)
            if key in seen:  # 多帳戶同一標的合併
                h = seen[key]
                h["quantity"] += pos.quantity
                h["market_value"] += mv_cny
                h["unrealized_pnl"] += pnl_cny
            else:
                h = {
                    "symbol": stock.symbol,
                    "market": stock.market,
                    "name": stock.name,
                    "quantity": pos.quantity,
                    "fx": rate,
                    "market_value": mv_cny,
                    "unrealized_pnl": pnl_cny,
                    "strategy_code": pos.trading_style or "",
                }
                seen[key] = h
                out.append(h)
    return out


@router.get("/portfolio/diagnostics")
def portfolio_diagnostics(db: Session = Depends(get_db)):
    """真實持倉組合診斷:集中度(HHI)/最大單倉/市場分佈/風險提示(只讀)。"""
    from src.modules.portfolio.portfolio_diagnostics import diagnose_positions

    return diagnose_positions(_gather_holdings(db))


@router.get("/portfolio/benchmark")
def portfolio_benchmark(
    days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)
):
    """真實持倉組合 vs 基準:超額收益/資訊比率/相對回檔 + 歸一化淨值曲線。"""
    from src.modules.portfolio.portfolio_benchmark import (
        DEFAULT_BENCHMARK,
        build_portfolio_benchmark,
    )

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"empty": True, "reason": "no_holdings"}
    ckey = f"bench:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}
    res = build_portfolio_benchmark(holdings, days=days, benchmark_code=bcode)
    if not res:
        # 失敗/資料不足不快取,下輪可重試(由 K 線負快取兜住打爆)
        return {"empty": True, "reason": "insufficient_data"}
    _PORTFOLIO_RESULT_CACHE.set(ckey, res)
    return res


@router.get("/portfolio/todos")
def portfolio_todos(db: Session = Depends(get_db)):
    """首頁空態待辦:持倉但未設提醒 / 提醒即將到期(可行動,盤後也不空)。"""
    todos: list[dict] = []
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    held_ids = {p.stock_id for acc in accounts for p in acc.positions}
    if held_ids:
        ruled = {
            r.stock_id
            for r in db.query(PriceAlertRule)
            .filter(PriceAlertRule.enabled == True, PriceAlertRule.stock_id.in_(held_ids))  # noqa: E712
            .all()
        }
        for sid in held_ids - ruled:
            stock = db.query(Stock).filter(Stock.id == sid).first()
            if stock:
                todos.append(
                    {
                        "type": "no_alert",
                        "symbol": stock.symbol,
                        "market": stock.market,
                        "message": f"{stock.name} 持倉中,未設價格提醒",
                    }
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    expiring = (
        db.query(PriceAlertRule)
        .filter(
            PriceAlertRule.enabled == True,  # noqa: E712
            PriceAlertRule.expire_at.isnot(None),
            PriceAlertRule.expire_at >= now,
            PriceAlertRule.expire_at <= soon,
        )
        .all()
    )
    for r in expiring:
        stock = db.query(Stock).filter(Stock.id == r.stock_id).first()
        todos.append(
            {
                "type": "alert_expiring",
                "symbol": stock.symbol if stock else "",
                "market": stock.market if stock else "CN",
                "message": f"{(r.name or '提醒')} 即將到期",
            }
        )

    return {"todos": todos[:10], "count": len(todos)}


@router.get("/portfolio/attribution")
def portfolio_attribution(days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)):
    """近 days 日各持倉對組合收益的貢獻(誰拖累/貢獻),降序。"""
    from src.modules.portfolio.portfolio_benchmark import DEFAULT_BENCHMARK, build_attribution

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"items": []}
    ckey = f"attr:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"items": []}
    items = build_attribution(holdings, days=days, benchmark_code=bcode)
    result = {"items": items}
    if items:  # 空結果不快取,下輪可重試
        _PORTFOLIO_RESULT_CACHE.set(ckey, result)
    return result


def _gather_account_totals(db: Session, *, market_value: float) -> dict:
    """Use the same enabled-account scope as holdings; cash is stored in CNY.

    Reuse the already-valued holdings instead of fetching quotes a second time.
    Non-positive equity has no meaningful exposure ratio (not zero exposure).
    """
    cash = float(db.query(func.sum(Account.available_funds)).filter(
        Account.enabled == True  # noqa: E712
    ).scalar() or 0.0)
    total = market_value + cash
    return {
        "available_funds": round(cash, 2),
        "total_assets": round(total, 2),
        "equity_ratio": market_value / total if total > 0 else None,
    }


@router.post("/portfolio/ai-review")
async def portfolio_ai_review(model_id: int | None = None, db: Session = Depends(get_db)):
    """組合 AI 體檢:診斷+基準+歸因 → 敘述結論 + 調倉建議(只讀,不下單)。"""
    from src.modules.portfolio.portfolio_benchmark import build_attribution, build_portfolio_benchmark
    from src.modules.portfolio.portfolio_diagnostics import diagnose_positions
    from src.platform.ai.ai_failover import get_configured_failover_client

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}

    diag = diagnose_positions(holdings)
    totals = _gather_account_totals(db, market_value=diag["total_market_value"])
    bench = build_portfolio_benchmark(holdings, days=60) or {}
    attr = build_attribution(holdings, days=60)
    top = attr[:3]
    worst = list(reversed(attr[-3:])) if len(attr) > 3 else []

    lines = [
        f"持倉 {diag['position_count']} 只,總市值 {diag['total_market_value']:.0f},未實現獲利 {diag['total_unrealized_pnl']:.0f}",
        f"持倉內部集中度 HHI {diag['hhi']},最大單倉佔已投資金額 {diag['max_weight'] * 100:.0f}%",
        f"啟用帳戶總資產 {totals['total_assets']:.0f} CNY（現金/可用資金 {totals['available_funds']:.0f} CNY）",
        (f"總資產敞口：權益類倉位佔總資產 {totals['equity_ratio'] * 100:.1f}%"
         if totals['equity_ratio'] is not None else "總資產敞口：總資產非正，比例不可計算"),
    ]
    if bench.get("excess_return") is not None:
        lines.append(
            f"近60日 vs {bench.get('benchmark_label', '基準')}:超額 {bench['excess_return']}%"
            f"(組合 {bench.get('portfolio_return')}% / 基準 {bench.get('benchmark_return')}%),"
            f"相對回檔 {bench.get('relative_drawdown')}%"
        )
    if diag.get("by_market"):
        lines.append("持倉內部市場分佈（市值 CNY）:" + ", ".join(f"{k} {v:.0f}" for k, v in diag["by_market"].items()))
    if diag.get("alerts"):
        lines.append("風險提示:" + "; ".join(diag["alerts"]))
    if top:
        lines.append("貢獻最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in top))
    if worst:
        lines.append("拖累最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in worst))

    system_prompt = (
        "你是穩健的組合顧問。基於給定的組合診斷/基準對比/個股歸因,給一段簡短體檢 + 可執行調倉建議,"
        "務必區分持倉內部集中度（已投資金額中的分佈）和相對總資產的實際權益敞口。"
        "不得用持倉內部的集中度百分比形容總資產敞口。現金按啟用帳戶已錄入的可用資金計算，未核驗券商餘額。"
        "總資產非正時不得編造敞口比例；輸出必須包含‘持倉內部集中度’和‘總資產敞口’兩項。"
        "只讀分析、不下單、不承諾收益。嚴格格式:\n體檢: 一句話總評\n建議:\n- (2~3 條具體可執行)\n風險: 一句話最大風險"
    )
    user_content = "組合概況:\n" + "\n".join(lines)
    try:
        content = await get_configured_failover_client(db, model_id).chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI 體檢失敗: {e}")

    return {"content": content, "top": top, "worst": worst, "diagnostics": diag, "benchmark": bench, "account_totals": totals}
