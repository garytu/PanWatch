"""市場/資金面 vendor:龍虎榜 / 融資融券 / 股東戶數 / 分紅,均走東財 datacenter 同構介面
(datacenter-web.eastmoney.com/api/data/v1/get,reportName + filter + columns=ALL)。

欄位索引按任務方給出的列名(源自 a-stock SKILL.md)+ 現有 fundamentals.py 取數骨架校準,
未在沙箱內實抓驗證——標"待實抓校準"的欄位上線前需用真實回應複核。拿不到的欄位一律 None,
不偽造、不用無參 now()/random 填充數值。

- 龍虎榜(dragon_tiger):**市場級**,不按 symbol,按 date 過濾當日全部上榜明細。
- 融資融券(margin)/股東戶數(shareholders)/分紅(dividend):**按 symbol**,逐只請求
  (datacenter 該幾個 report 均只支援單程式碼 filter,無法一次性批次多隻)。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import DividendItem, DragonTigerItem, MarginItem, ShareholderItem
from marketdata.vendors.base import (
    DividendVendor,
    DragonTigerVendor,
    MarginVendor,
    ShareholdersVendor,
)

logger = logging.getLogger(__name__)

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DATACENTER_HOST = "datacenter-web.eastmoney.com"


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _datacenter_get(report: str, filter_str: str, sort_col: str, page_size: int = 50) -> list[dict]:
    """東財 datacenter 統一請求 helper:GET datacenter-web.eastmoney.com/api/data/v1/get。

    防禦:請求失敗或回應結構不含 result.data 一律返回 []。
    """
    resp = market_get(
        _DATACENTER_URL,
        host_key=_DATACENTER_HOST,
        params={
            "reportName": report,
            "columns": "ALL",
            "filter": filter_str,
            "pageNumber": 1,
            "pageSize": page_size,
            "sortColumns": sort_col,
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
        parse="json",
        retries=2,
        timeout=10,
        log_label=f"東財市場資金面/{report}",
    )
    if not resp or not isinstance(resp, dict):
        return []
    result = resp.get("result")
    if not result or not isinstance(result, dict):
        return []
    data = result.get("data")
    return data if isinstance(data, list) else []


# ============================== 龍虎榜(市場級) ==============================

_REPORT_DRAGON_TIGER = "RPT_DAILYBILLBOARD_DETAILSNEW"


class EastmoneyDragonTigerVendor(DragonTigerVendor):
    """龍虎榜:市場級,fetch 忽略 symbols,按 config["date"](YYYY-MM-DD)過濾當日明細。

    不猜測"今天"——呼叫方未顯式給 date 時直接返回 [],避免包內出現無參 now()。
    """

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DragonTigerItem]:
        date = (config or {}).get("date")
        if not date:
            return []
        filter_str = f"(TRADE_DATE>='{date}')(TRADE_DATE<='{date}')"
        rows = _datacenter_get(_REPORT_DRAGON_TIGER, filter_str, "BILLBOARD_NET_AMT", page_size=500)

        out: list[DragonTigerItem] = []
        for row in rows:
            try:
                out.append(
                    DragonTigerItem(
                        trade_date=str(row.get("TRADE_DATE") or date)[:10],
                        symbol=str(row.get("SECURITY_CODE") or ""),
                        name=str(row.get("SECURITY_NAME_ABBR") or ""),
                        reason=row.get("EXPLANATION"),
                        close=_to_float(row.get("CLOSE_PRICE")),
                        change_pct=_to_float(row.get("CHANGE_RATE")),
                        net_buy=_to_float(row.get("BILLBOARD_NET_AMT")),
                        buy_amt=_to_float(row.get("BILLBOARD_BUY_AMT")),
                        sell_amt=_to_float(row.get("BILLBOARD_SELL_AMT")),
                        turnover_pct=_to_float(row.get("TURNOVERRATE")),
                    )
                )
            except Exception as e:
                logger.debug(f"解析龍虎榜行失敗: {e}")
                continue
        return out


# ============================== 融資融券(按 symbol) ==============================

_REPORT_MARGIN = "RPTA_WEB_RZRQ_GGMX"


class EastmoneyMarginVendor(MarginVendor):
    """融資融券:按 symbol 逐只請求近期明細,取最新一條(sortTypes=-1 已降序 → data[0])。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[MarginItem]:
        if not symbols:
            return []
        out: list[MarginItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SCODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_MARGIN, filter_str, "DATE", page_size=30)
                if not rows:
                    continue
                row = rows[0]
                out.append(
                    MarginItem(
                        date=str(row.get("DATE") or "")[:10],
                        symbol=sym.code,
                        rz_balance=_to_float(row.get("RZYE")),
                        rz_buy=_to_float(row.get("RZMRE")),
                        rz_repay=_to_float(row.get("RZCHE")),
                        rq_balance=_to_float(row.get("RQYE")),
                        rq_sell_vol=_to_float(row.get("RQMCL")),
                        rq_repay_vol=_to_float(row.get("RQCHL")),
                        total_balance=_to_float(row.get("RZRQYE")),
                    )
                )
            except Exception as e:
                logger.debug(f"東財融資融券取數異常 symbol={sym.code}: {e}")
                continue
        return out


# ============================== 股東戶數(按 symbol) ==============================

_REPORT_SHAREHOLDERS = "RPT_HOLDERNUMLATEST"


class EastmoneyShareholdersVendor(ShareholdersVendor):
    """股東戶數:按 symbol 逐只請求,取最新一期。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[ShareholderItem]:
        if not symbols:
            return []
        out: list[ShareholderItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SECURITY_CODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_SHAREHOLDERS, filter_str, "END_DATE", page_size=1)
                if not rows:
                    continue
                row = rows[0]
                out.append(
                    ShareholderItem(
                        report_date=str(row.get("END_DATE") or "")[:10],
                        symbol=sym.code,
                        holder_num=_to_int(row.get("HOLDER_NUM")),
                        change_num=_to_int(row.get("HOLDER_NUM_CHANGE")),
                        change_ratio=_to_float(row.get("HOLDER_NUM_RATIO")),
                        avg_shares=_to_float(row.get("AVG_FREE_SHARES")),
                    )
                )
            except Exception as e:
                logger.debug(f"東財股東戶數取數異常 symbol={sym.code}: {e}")
                continue
        return out


# ============================== 分紅(按 symbol) ==============================

_REPORT_DIVIDEND = "RPT_SHAREBONUS_DET"


class EastmoneyDividendVendor(DividendVendor):
    """分紅:按 symbol 逐只請求,返回該只全部分紅歷史(可能多條)。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DividendItem]:
        if not symbols:
            return []
        out: list[DividendItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SECURITY_CODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_DIVIDEND, filter_str, "EX_DIVIDEND_DATE", page_size=20)
                for row in rows:
                    try:
                        out.append(
                            DividendItem(
                                ex_date=str(row.get("EX_DIVIDEND_DATE") or "")[:10],
                                symbol=sym.code,
                                dividend_per_share=_to_float(row.get("PRETAX_BONUS_RMB")),
                                transfer_ratio=_to_float(row.get("TRANSFER_RATIO")),
                                bonus_ratio=_to_float(row.get("BONUS_RATIO")),
                                progress=str(row.get("ASSIGN_PROGRESS") or ""),
                            )
                        )
                    except Exception as e:
                        logger.debug(f"解析分紅行失敗 symbol={sym.code}: {e}")
                        continue
            except Exception as e:
                logger.debug(f"東財分紅取數異常 symbol={sym.code}: {e}")
                continue
        return out
