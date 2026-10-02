"""基本面/財務 vendor:騰訊(CN,~陣列)+ 東財(CN push2 / US·HK datacenter GMAININDICATOR)。

欄位索引/鍵名按 a-stock/global SKILL 記錄 + 現有 vendor(tencent.py/eastmoney.py)取數骨架校準,
未在沙箱內實抓驗證——標"待實抓校準"的欄位上線前需用真實回應複核。拿不到的欄位一律 None,
不偽造、不用無參 now()/random 填充數值。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import Fundamentals
from marketdata.vendors.base import FundamentalsVendor
from marketdata.vendors.tencent import _fetch_lines

logger = logging.getLogger(__name__)


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================== 騰訊(CN) ==============================

def _parse_fundamentals_line(line: str, market: str) -> Fundamentals | None:
    """解析騰訊 qt.gtimg `~` 陣列行為 Fundamentals。

    索引:idx1=name、idx39=pe_ttm、idx44=circulating_market_value(億)、
    idx45=total_market_value(億)、idx46=pb、idx52=pe_static。
    注:44/45(流通/總市值)順序對齊本包 tencent.py Quote 解析的既有約定
    (44=流通、45=總),保證包內一致;兩處均未經真實回應交叉核對,待實抓校準。
    """
    if '=""' in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        parts = value.rstrip('";').split("~")
        if len(parts) < 3:
            return None

        symbol = parts[2]
        if "." in symbol and not symbol.startswith("."):
            symbol = symbol.split(".")[0]

        name = parts[1] if len(parts) > 1 else ""
        pe_ttm = _to_float(parts[39]) if len(parts) > 39 else None
        circulating_market_value = _to_float(parts[44]) if len(parts) > 45 else None
        total_market_value = _to_float(parts[45]) if len(parts) > 45 else None
        pb = _to_float(parts[46]) if len(parts) > 46 else None
        pe_static = _to_float(parts[52]) if len(parts) > 52 else None

        return Fundamentals(
            symbol=symbol,
            market=market,
            name=name,
            pe_ttm=pe_ttm,
            pe_static=pe_static,
            pb=pb,
            total_market_value=total_market_value,
            circulating_market_value=circulating_market_value,
        )
    except (ValueError, IndexError) as e:
        logger.debug(f"解析騰訊基本面失敗: {e}")
        return None


class TencentFundamentalsVendor(FundamentalsVendor):
    name = "tencent"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Fundamentals]:
        cn_symbols = [s for s in symbols if s.market == Market.CN]
        if not cn_symbols:
            return []
        market = cn_symbols[0].market.value
        codes = [s.to_tencent() for s in cn_symbols]
        out: list[Fundamentals] = []
        for line in _fetch_lines(codes):
            f = _parse_fundamentals_line(line, market)
            if f:
                out.append(f)
        return out


# ============================== 東財 ==============================

_PUSH2_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_PUSH2_HOST = "push2.eastmoney.com"
_PUSH2_FIELDS = "f57,f58,f84,f85,f116,f117"
_PUSH2_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DATACENTER_HOST = "datacenter-web.eastmoney.com"
_DATACENTER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}
_REPORT_NAME_US = "RPT_USF10_FN_GMAININDICATOR"
_REPORT_NAME_HK = "RPT_HKF10_FN_GMAININDICATOR"


def _fetch_cn(sym: Symbol) -> Fundamentals | None:
    """CN:push2 stock/get。PE/PB 該端點未提供,留 None(quote 端另有源,這裡不重複取)。"""
    payload = market_get(
        _PUSH2_URL,
        host_key=_PUSH2_HOST,
        min_interval_s=0.2,
        params={"secid": sym.to_eastmoney_secid(), "fields": _PUSH2_FIELDS},
        headers=_PUSH2_HEADERS,
        timeout=8,
        retries=2,
        parse="json",
        log_label="東財基本面",
        symbol=sym.code,
    )
    if not payload:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not data:
        return None

    total_mv = _to_float(data.get("f116"))
    circ_mv = _to_float(data.get("f117"))
    return Fundamentals(
        symbol=str(data.get("f57") or sym.code),
        market=sym.market.value,
        name=str(data.get("f58") or ""),
        total_shares=_to_float(data.get("f84")),
        float_shares=_to_float(data.get("f85")),
        total_market_value=(total_mv / 1e8) if total_mv is not None else None,
        circulating_market_value=(circ_mv / 1e8) if circ_mv is not None else None,
    )


def _hk_secucode(code: str) -> str:
    """HK SECUCODE:補零到 5 位 + .HK(參考 symbol.py to_yfinance 的補零慣例,該處補 4 位是 yfinance
    專用格式,SECUCODE 按東財 F10 慣例補 5 位)。"""
    if code.isdigit():
        return f"{int(code):05d}.HK"
    return f"{code}.HK"


def _fetch_gmainindicator(report_name: str, secucode: str, sym: Symbol) -> dict | None:
    params = {
        "reportName": report_name,
        "filter": f'(SECUCODE="{secucode}")',
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "pageSize": "1",
        "columns": "ALL",
    }
    payload = market_get(
        _DATACENTER_URL,
        host_key=_DATACENTER_HOST,
        min_interval_s=0.2,
        params=params,
        headers=_DATACENTER_HEADERS,
        timeout=8,
        retries=2,
        parse="json",
        log_label="東財財務指標",
        symbol=sym.code,
    )
    if not payload or not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not result:
        return None
    rows = result.get("data")
    if not rows:
        return None
    return rows[0]


def _row_to_fundamentals(row: dict, sym: Symbol, *, is_hk: bool) -> Fundamentals:
    """GMAININDICATOR 一行 → Fundamentals。欄位名待實抓校準(尤其 net_profit_yoy 的具體列名未確認,
    這裡防禦性嘗試常見候選列名,取不到則 None)。"""
    report_date = str(row.get("REPORT_DATE") or "")[:10]
    net_profit_yoy = _to_float(
        row.get("PARENT_NETPROFIT_YOY")
        if row.get("PARENT_NETPROFIT_YOY") is not None
        else row.get("NET_PROFIT_YOY")
    )
    f = Fundamentals(
        symbol=sym.code,
        market=sym.market.value,
        eps=_to_float(row.get("BASIC_EPS")),
        roe=_to_float(row.get("ROE_AVG")),
        revenue=_to_float(row.get("OPERATE_INCOME")),
        net_profit=_to_float(row.get("PARENT_HOLDER_NETPROFIT")),
        gross_margin=_to_float(row.get("GROSS_PROFIT_RATIO")),
        net_margin=_to_float(row.get("NET_PROFIT_RATIO")),
        revenue_yoy=_to_float(row.get("OPERATE_INCOME_YOY")),
        net_profit_yoy=net_profit_yoy,
        report_date=report_date,
    )
    if is_hk:
        f.bps = _to_float(row.get("BPS"))
        f.dividend_yield = _to_float(row.get("DIVI_RATIO"))
    return f


def _fetch_us(sym: Symbol) -> Fundamentals | None:
    """US:SECUCODE 字首未知,先試 NASDAQ(.O),空則再試 NYSE(.N),都空則該只 None。"""
    for suffix in ("O", "N"):
        secucode = f"{sym.code}.{suffix}"
        row = _fetch_gmainindicator(_REPORT_NAME_US, secucode, sym)
        if row:
            return _row_to_fundamentals(row, sym, is_hk=False)
    return None


def _fetch_hk(sym: Symbol) -> Fundamentals | None:
    secucode = _hk_secucode(sym.code)
    row = _fetch_gmainindicator(_REPORT_NAME_HK, secucode, sym)
    if not row:
        return None
    return _row_to_fundamentals(row, sym, is_hk=True)


class EastmoneyFundamentalsVendor(FundamentalsVendor):
    name = "eastmoney"
    supports_markets = {"CN", "US", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Fundamentals]:
        if not symbols:
            return []
        out: list[Fundamentals] = []
        for sym in symbols:
            try:
                if sym.market == Market.CN:
                    f = _fetch_cn(sym)
                elif sym.market == Market.US:
                    f = _fetch_us(sym)
                elif sym.market == Market.HK:
                    f = _fetch_hk(sym)
                else:
                    continue
            except Exception as e:
                logger.debug(f"東財基本面取數異常 symbol={sym.code}: {e}")
                continue
            if f:
                out.append(f)
        return out
