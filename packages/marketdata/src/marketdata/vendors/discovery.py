"""發現(東財熱門榜)vendor:單源、市場級、非 symbol 模型。

移植自 PanWatch src/collectors/discovery_collector.py 的
fetch_hot_stocks(L55-105)/fetch_hot_boards(L107-149)/fetch_board_stocks(L151-196)/
_get_json(L198-248):fid/fields/fs/params 計算與 f-code 欄位對映(f12/f14/f2/f3/f4/f5/f6)
逐一照搬。原實現是 async(httpx.AsyncClient);此處改為同步 market_get。

不繼承 marketdata.vendors.base.Vendor —— discovery 是市場級、單源、非 symbol 的取數,
硬套 symbol-based 失敗轉移 Engine 是設計錯配,故不進 Engine/不進 DataSource taxonomy。
vendor 內不做快取(TTL 快取留宿主 collector)。
"""
from __future__ import annotations

from typing import Any

from marketdata.http import market_get
from marketdata.types import HotBoard, HotStock

# push2 occasionally accepts the TCP connection and closes it before sending
# headers in desktop/NAS proxy environments. push2delay exposes the same clist
# contract and is reachable both directly and through the configured proxy.
_STOCKS_API = "https://push2delay.eastmoney.com/api/qt/clist/get"
_BOARDS_API = "https://push2delay.eastmoney.com/api/qt/clist/get"
_HOST_KEY = "push2.eastmoney.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def _normalize_diff(data: dict | None) -> list[dict]:
    """東財 clist 的 diff 欄位可能是 list,也可能是 dict(按 index 為 key)。統一成 list。"""
    diff = ((data or {}).get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        return list(diff.values())
    return diff


class DiscoveryVendor:
    """東財熱門榜(股票/板塊/板塊成分)。不繼承 Vendor(市場級、非 symbol)。"""

    def hot_stocks(
        self,
        *,
        market: str = "CN",
        mode: str = "turnover",
        limit: int = 20,
        proxy: str | None = None,
    ) -> list[HotStock]:
        market = (market or "CN").upper()

        fid = "f6" if mode == "turnover" else "f3"
        fields = "f12,f14,f2,f3,f6,f5"
        if market == "CN":
            fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # A-share
        elif market == "HK":
            fs = "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2"  # HK
        elif market == "US":
            fs = "m:105,m:106,m:107"  # US
        else:
            return []

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = self._get_json(_STOCKS_API, params=params, proxy=proxy)
        result: list[HotStock] = []
        for it in _normalize_diff(data):
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market=market,
                        name=str(it.get("f14") or "").strip(),
                        price=it.get("f2"),
                        change_pct=it.get("f3"),
                        turnover=it.get("f6"),
                        volume=it.get("f5"),
                    )
                )
            except Exception:
                continue
        return result

    def hot_boards(
        self,
        *,
        market: str = "CN",
        mode: str = "gainers",
        limit: int = 12,
        proxy: str | None = None,
    ) -> list[HotBoard]:
        if market != "CN":
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f4,f6"
        fs = "m:90+t:2"  # industry boards

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = self._get_json(_BOARDS_API, params=params, proxy=proxy)
        result: list[HotBoard] = []
        for it in _normalize_diff(data):
            try:
                result.append(
                    HotBoard(
                        code=str(it.get("f12") or "").strip(),
                        name=str(it.get("f14") or "").strip(),
                        change_pct=it.get("f3"),
                        change_amount=it.get("f4"),
                        turnover=it.get("f6"),
                    )
                )
            except Exception:
                continue
        return result

    def board_stocks(
        self,
        *,
        board_code: str,
        mode: str = "gainers",
        limit: int = 20,
        proxy: str | None = None,
    ) -> list[HotStock]:
        code = (board_code or "").strip()
        if not code:
            return []

        fid = "f3" if mode in ("gainers", "hot") else "f6"
        fields = "f12,f14,f2,f3,f6,f5"
        fs = f"b:{code}"

        params = {
            "pn": 1,
            "pz": max(1, min(int(limit), 100)),
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }

        data = self._get_json(_STOCKS_API, params=params, proxy=proxy)
        result: list[HotStock] = []
        for it in _normalize_diff(data):
            try:
                result.append(
                    HotStock(
                        symbol=str(it.get("f12") or "").strip(),
                        market="CN",
                        name=str(it.get("f14") or "").strip(),
                        price=it.get("f2"),
                        change_pct=it.get("f3"),
                        turnover=it.get("f6"),
                        volume=it.get("f5"),
                    )
                )
            except Exception:
                continue
        return result

    def _get_json(self, url: str, *, params: dict, proxy: str | None) -> dict[str, Any]:
        data = market_get(
            url,
            host_key=_HOST_KEY,
            params=params,
            headers=_HEADERS,
            proxy=proxy,
            verify=False,
            parse="json",
            retries=1,
            timeout=10,
            min_interval_s=0.0,
            log_label="發現榜單",
        )
        return data or {}
