from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List

from sqlalchemy.orm import Session

from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.automation.suggestion_pool import get_latest_suggestions
from src.modules.assistant.legacy_chat_tools import (
    build_stock_context,
    fetch_realtime_context,
    fetch_technical_context,
)
from src.platform.ai.ai_failover import get_configured_failover_client
from src.platform.marketdata.collectors.market_http import TTLCache
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# 公告解讀快取(公告不變,長 TTL)
_ANN_CACHE = TTLCache(default_ttl_sec=21600)  # 6h

router = APIRouter()


class InsightItem(BaseModel):
    symbol: str = Field(..., description="股票程式碼")
    market: str = Field(..., description="市場: CN/HK/US")


class InsightsBatchRequest(BaseModel):
    items: List[InsightItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支援的市場: {market}")


@router.post("/batch")
def insights_batch(payload: InsightsBatchRequest):
    """聚合返回行情 + K線摘要 + 最新建議"""
    if not payload.items:
        return []

    # 1) 批次行情（按市場）
    market_items: dict[MarketCode, list[str]] = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        market_items.setdefault(market_code, []).append(it.symbol)

    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        try:
            items = md_quote_rows(symbols, market_code.value)
        except Exception:
            items = []
        quotes_by_market[market_code] = {item["symbol"]: item for item in items}

    # 2) K線摘要（逐只，帶 60s 簡易快取）
    kline_by_symbol: dict[str, dict] = {}
    now = time.time()
    TTL = 60.0
    # module-level cache
    global _KLINE_CACHE
    try:
        _KLINE_CACHE
    except NameError:
        _KLINE_CACHE = {}
    for it in payload.items:
        market_code = _parse_market(it.market)
        cache_key = f"{market_code.value}:{it.symbol}"
        cached = _KLINE_CACHE.get(cache_key)
        summary = None
        if cached and (now - cached[0] < TTL):
            summary = cached[1]
        else:
            try:
                collector = KlineCollector(market_code)
                summary = collector.get_kline_summary(it.symbol)
            except Exception:
                summary = {}
            _KLINE_CACHE[cache_key] = (now, summary)
        kline_by_symbol[cache_key] = summary

    # 3) 最新建議（建議池）
    stock_keys = [(it.symbol, _parse_market(it.market).value) for it in payload.items]
    latest_sugs = get_latest_suggestions(stock_keys=stock_keys, include_expired=False)

    # 4) 合併返回
    results = []
    for it in payload.items:
        market_code = _parse_market(it.market)
        quote = quotes_by_market.get(market_code, {}).get(it.symbol)
        results.append({
            "symbol": it.symbol,
            "market": market_code.value,
            "quote": {
                "name": quote.get("name") if quote else None,
                "current_price": quote.get("current_price") if quote else None,
                "change_pct": quote.get("change_pct") if quote else None,
                "open_price": quote.get("open_price") if quote else None,
                "high_price": quote.get("high_price") if quote else None,
                "low_price": quote.get("low_price") if quote else None,
                "volume": quote.get("volume") if quote else None,
                "turnover": quote.get("turnover") if quote else None,
            },
            "kline_summary": kline_by_symbol.get(f"{market_code.value}:{it.symbol}", {}),
            "suggestion": latest_sugs.get(f"{market_code.value}:{it.symbol}"),
        })

    return results


class AddPositionEvalRequest(BaseModel):
    symbol: str
    market: str = "CN"
    current_quantity: float = Field(0, ge=0, description="當前持倉股數(0=建倉)")
    current_cost: float = Field(0, ge=0, description="當前成本(單價)")
    add_quantity: float = Field(..., gt=0, description="加碼股數")
    add_price: float = Field(..., gt=0, description="加碼價格")
    model_id: int | None = None


_VERDICTS = ("不適合", "謹慎", "適合")  # 先長後短:'不適合' 含 '適合',順序不能反


def _parse_verdict(text: str) -> str:
    """從 AI 回覆粗解析結論標籤;命中不到返回'未知'。"""
    head = (text or "")[:120]
    for v in _VERDICTS:
        if v in head:
            return v
    return "未知"


async def _fetch_fundamental_context(symbol: str, market: str) -> str:
    """基本面摘要:PE / 周轉率 / 市值 / 今日振幅(取自即時行情,失敗返回空)。"""
    try:
        mc = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
        rows = await asyncio.to_thread(md_quote_rows, [symbol], mc.value)
        if not rows:
            return ""
        q = rows[0]
        parts: list[str] = []
        if q.get("pe_ratio") not in (None, 0):
            parts.append(f"市盈率 {q['pe_ratio']}")
        if q.get("turnover_rate") not in (None, 0):
            parts.append(f"周轉率 {q['turnover_rate']}%")
        if q.get("circulating_market_value"):
            parts.append(f"流通市值 {q['circulating_market_value']}億")
        if q.get("total_market_value"):
            parts.append(f"總市值 {q['total_market_value']}億")
        hi, lo, pc = q.get("high_price"), q.get("low_price"), q.get("prev_close")
        if hi and lo and pc:
            parts.append(f"今日振幅 {(hi - lo) / pc * 100:.2f}%")
        return ("基本面:" + "，".join(parts)) if parts else ""
    except Exception as e:
        logger.debug(f"基本面獲取失敗 {symbol}: {e}")
        return ""


async def _fetch_message_context(db: Session, symbol: str, market: str) -> str:
    """訊息面摘要:近 3 天新聞/公告標題 + 本地最近 AI 建議/分析(失敗降級為空)。"""
    parts: list[str] = []
    try:
        from src.platform.marketdata.collectors.news_collector import NewsCollector

        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        name = stock.name if stock else symbol
        collector = NewsCollector.from_database()
        items = await collector.fetch_all(
            symbols=[symbol], since_hours=72, symbol_names={symbol: name}
        )
        items = sorted(items, key=lambda x: x.publish_time, reverse=True)[:5]
        if items:
            lines = [
                f"- {it.title}（{it.publish_time.strftime('%m-%d')}）" for it in items
            ]
            parts.append("近期新聞/公告:\n" + "\n".join(lines))
    except Exception as e:
        logger.debug(f"訊息面新聞獲取失敗 {symbol}: {e}")

    try:
        ctx = build_stock_context(db, symbol, market)
        if ctx:
            parts.append(ctx)
    except Exception:
        pass

    return "\n\n".join(parts)


@router.post("/add-position-eval")
async def add_position_eval(req: AddPositionEvalRequest, db: Session = Depends(get_db)):
    """加碼快速評估:按服務埠徑算攤薄成本 + 讓 AI 給 適合/謹慎/不適合 結論。"""
    market = _parse_market(req.market).value
    cur_q = max(0.0, float(req.current_quantity or 0))
    cur_c = max(0.0, float(req.current_cost or 0))
    add_q = float(req.add_quantity)
    add_p = float(req.add_price)
    if add_q <= 0 or add_p <= 0:
        raise HTTPException(400, "加碼股數與價格必須大於 0")

    new_q = cur_q + add_q
    new_cost = (cur_q * cur_c + add_q * add_p) / new_q if new_q > 0 else add_p
    is_add = cur_q > 0 and cur_c > 0
    dilute_abs = (cur_c - new_cost) if is_add else 0.0
    dilute_pct = (dilute_abs / cur_c * 100) if is_add and cur_c > 0 else 0.0
    action = "加碼" if is_add else "建倉"

    # 上下文:即時行情 + 基本面 + 技術面 + 訊息面(新聞/公告/本地觀點)
    realtime = await fetch_realtime_context(req.symbol, market)
    fundamental = await _fetch_fundamental_context(req.symbol, market)
    technical = await fetch_technical_context(req.symbol, market)
    message = await _fetch_message_context(db, req.symbol, market)

    holding_line = (
        f"當前持倉 {cur_q:.0f} 股,成本(單價) {cur_c:.3f}"
        if is_add
        else "當前空倉(本次為建倉)"
    )
    dilute_line = f",較現成本攤薄 {dilute_abs:.3f}({dilute_pct:.2f}%)" if is_add else ""
    user_content = (
        f"標的 {market}:{req.symbol}\n"
        f"{holding_line}\n"
        f"擬{action} {add_q:.0f} 股 @ {add_p:.3f}\n"
        f"{action}後成本(單價) {new_cost:.3f}{dilute_line}\n"
        + (f"{realtime}\n" if realtime else "")
        + (f"{fundamental}\n" if fundamental else "")
        + (f"{technical}\n" if technical else "")
        + (f"{message}\n" if message else "")
        + f"請綜合估值/基本面與訊息面,評估這次{action}是否合適。"
    )
    system_prompt = (
        "你是謹慎務實的股票交易助手。綜合使用者給出的持倉、價格、基本面、技術面與訊息面資訊,"
        f"評估這次{action}是否合適,不臆造資料、不做收益承諾。\n"
        "嚴格按以下格式輸出,簡潔:\n"
        "結論: 適合 / 謹慎 / 不適合(三選一)\n"
        "理由:\n- (2~3 條,結合攤薄成本、估值/基本面、技術面與訊息面)\n"
        "風險: (一句話最大風險)"
    )

    try:
        client = get_configured_failover_client(db, req.model_id)
        content = await client.chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI 評估失敗: {e}")

    return {
        "symbol": req.symbol,
        "market": market,
        "action": action,
        "new_cost": round(new_cost, 4),
        "dilute_abs": round(dilute_abs, 4),
        "dilute_pct": round(dilute_pct, 4),
        "total_quantity": new_q,
        "total_invested": round(new_q * new_cost, 2),
        "verdict": _parse_verdict(content),
        "content": content,
    }


# ── 公告/財報 利好利空解讀(Phase B)──────────────────────────────────────
_ANN_TONES = ("利好", "利空", "中性")


def _parse_tone(text: str) -> str:
    head = (text or "")[:60]
    for t in _ANN_TONES:
        if t in head:
            return t
    return "中性"


async def _fetch_recent_announcements(symbol: str, name: str, limit: int = 5) -> list[dict]:
    """取近 7 天公告/新聞(優先東財公告),失敗返回 []。"""
    try:
        from src.platform.marketdata.collectors.news_collector import NewsCollector

        items = await NewsCollector.from_database().fetch_all(
            symbols=[symbol], since_hours=168, symbol_names={symbol: name}
        )
        anns = [it for it in items if it.source == "eastmoney"] or items
        anns = sorted(anns, key=lambda x: x.publish_time, reverse=True)[:limit]
        return [
            {
                "title": a.title,
                "time": a.publish_time.strftime("%Y-%m-%d %H:%M"),
                "content": (a.content or "")[:200],
            }
            for a in anns
        ]
    except Exception as e:
        logger.debug(f"公告獲取失敗 {symbol}: {e}")
        return []


class AnnouncementEvalRequest(BaseModel):
    symbol: str
    market: str = "CN"
    model_id: int | None = None


@router.post("/announcement-eval")
async def announcement_eval(req: AnnouncementEvalRequest, db: Session = Depends(get_db)):
    """近期公告 → AI 逐條判利好/利空/中性 + 一句話。降級:無全文則用標題。"""
    market = _parse_market(req.market).value
    cache_key = f"{market}:{req.symbol}"
    cached = _ANN_CACHE.get(cache_key)
    if cached is not None:
        return cached

    stock = db.query(Stock).filter(Stock.symbol == req.symbol).first()
    name = stock.name if stock else req.symbol
    anns = await _fetch_recent_announcements(req.symbol, name)
    if not anns:
        result = {"symbol": req.symbol, "market": market, "items": []}
        _ANN_CACHE.set(cache_key, result, ttl_sec=600)  # 無資料短快取
        return result

    top = anns[:3]
    listing = "\n".join(
        f"{i + 1}. {a['title']}（{a['time']}）" + (f" — {a['content']}" if a["content"] else "")
        for i, a in enumerate(top)
    )
    system_prompt = (
        "你是 A股公告解讀助手。對每條公告判斷對股價的影響傾向(利好/利空/中性)並給一句話理由,"
        "只依據給定資訊、不臆造。嚴格逐條一行,格式: 序號|利好或利空或中性|一句話"
    )
    user_content = f"標的 {name}({market}:{req.symbol}) 近期公告:\n{listing}"
    try:
        content = await get_configured_failover_client(db, req.model_id).chat(
            system_prompt, user_content, temperature=0.2
        )
    except Exception as e:
        raise HTTPException(502, f"AI 公告解讀失敗: {e}")

    tone_map: dict[int, tuple[str, str]] = {}
    for line in (content or "").splitlines():
        parts = line.split("|")
        idx_raw = parts[0].strip().rstrip(".、) ") if parts else ""
        if len(parts) >= 3 and idx_raw.isdigit():
            tone_map[int(idx_raw) - 1] = (_parse_tone(parts[1]), parts[2].strip())

    items = []
    for i, a in enumerate(top):
        tone, note = tone_map.get(i, ("中性", ""))
        items.append({"title": a["title"], "time": a["time"], "tone": tone, "summary": note})
    result = {"symbol": req.symbol, "market": market, "items": items}
    _ANN_CACHE.set(cache_key, result)
    return result
