"""東財 CN 報價 vendor(quote 第二源)。push2 stock/get,單隻查詢,逐只迴圈取批次。

欄位對映經交叉核對 akshare `stock_ask_bid_em.py`(同一 push2 stock/get 端點,
fltt=2 預格式化模式下的欄位含義)+ 本倉 kline.py/capital_flow.py 東財現有慣例。
本 vendor 不傳 fltt/invt,取原始未格式化值,價格類欄位需 /10^f59 還原,
百分比類欄位(漲跌幅/周轉率/量比)固定 /100 還原。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Quote
from marketdata.vendors.base import QuoteVendor

logger = logging.getLogger(__name__)

_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_HOST = "push2.eastmoney.com"
_MIN_INTERVAL_S = 0.2
# f43 最新價 / f44 最高 / f45 最低 / f46 今開 / f47 成交量 / f48 成交額 / f50 量比 /
# f55(備用,CN 主用 f168) / f57 程式碼 / f58 名稱 / f59 小數位數 / f60 昨收 /
# f116 總市值 / f117 流通市值 / f168 周轉率 / f169 漲跌額 / f170 漲跌幅 / f171 振幅(未對映)
_FIELDS = "f43,f44,f45,f46,f47,f48,f50,f55,f57,f58,f59,f60,f116,f117,f168,f169,f170,f171"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scaled(value, decimals: int) -> float | None:
    """價格類欄位還原:raw / 10^decimals。"""
    v = _to_float(value)
    if v is None:
        return None
    try:
        return v / (10 ** decimals)
    except Exception:
        return None


def _pct(value) -> float | None:
    """百分比類欄位還原:raw / 100(漲跌幅/周轉率/量比,與小數位數無關)。"""
    v = _to_float(value)
    if v is None:
        return None
    return v / 100


def _parse_one(data: dict | None, market: str, fallback_code: str) -> Quote | None:
    if not data or data.get("f43") is None:
        return None
    dec = int(_to_float(data.get("f59")) or 2)
    price = _scaled(data.get("f43"), dec)
    if price is None or price <= 0:
        return None

    turnover_rate = _pct(data.get("f168"))
    if turnover_rate is None:
        turnover_rate = _pct(data.get("f55"))

    total_mv = _to_float(data.get("f116"))
    circ_mv = _to_float(data.get("f117"))

    return Quote(
        symbol=str(data.get("f57") or fallback_code),
        market=market,
        name=str(data.get("f58") or ""),
        current_price=price,
        prev_close=_scaled(data.get("f60"), dec),
        open_price=_scaled(data.get("f46"), dec),
        high_price=_scaled(data.get("f44"), dec),
        low_price=_scaled(data.get("f45"), dec),
        change_amount=_scaled(data.get("f169"), dec),
        change_pct=_pct(data.get("f170")),
        volume=_to_float(data.get("f47")),
        turnover=_to_float(data.get("f48")),
        turnover_rate=turnover_rate,
        volume_ratio=_pct(data.get("f50")),
        pe_ratio=None,  # 未確認穩定欄位(f162 猜測,未經真實回應驗證),寧缺毋錯
        circulating_market_value=(circ_mv / 1e8) if circ_mv is not None else None,
        total_market_value=(total_mv / 1e8) if total_mv is not None else None,
    )


class EastmoneyQuoteVendor(QuoteVendor):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Quote]:
        if not symbols:
            return []
        out: list[Quote] = []
        for sym in symbols:
            if sym.market != Market.CN:
                continue
            payload = market_get(
                _URL,
                host_key=_HOST,
                min_interval_s=_MIN_INTERVAL_S,
                params={"secid": sym.to_eastmoney_secid(), "fields": _FIELDS},
                headers=_HEADERS,
                timeout=8,
                retries=2,
                parse="json",
                log_label="東財報價",
                symbol=sym.code,
            )
            if not payload:
                continue
            data = payload.get("data") if isinstance(payload, dict) else None
            q = _parse_one(data, sym.market.value, sym.code)
            if q:
                out.append(q)
        return out
