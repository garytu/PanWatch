"""北向資金 vendor:同花順(ths/hexin)當日分鐘累計淨買入,市場級(symbols 恆空)。

背景:東財 datacenter/push2 的北向資金介面(kamt)自 2024-08 起斷供(返回 NaN/0),
不可用。改走同花順 hexin 私有介面 `data.hexin.cn/market/hsgtApi/method/dayChart/`,
返回當日分鐘級累計淨買入序列,`hgt`(滬股通)/`sgt`(深股通),單位均為"億元"。

**待實抓校準**:沙箱代理會攔截 hexin,無法實抓驗證真實回應結構。以下解析按背景描述
("回應含當日分鐘序列,每點有時間 + hgt/sgt 累計值")盡力構造 + 逐層防禦 `.get()`,
拿不到就返回 []。真實結構上線前需用真實回應複核。

已知坑(SKILL 標註):`sgt`(深股通)近期資料不可靠,可能是 NaN 或量級異常(遠超合理的
"億元"範圍),必須容錯——異常時 sgt_net=None,不參與 total_net 計算,也不讓異常值汙染
hgt_net。絕不用無參 now()/time()/random 填充缺失的 date/time。
"""
from __future__ import annotations

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import NorthboundItem
from marketdata.vendors.base import NorthboundVendor as _NorthboundVendorBase

_HEXIN_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
_HEXIN_HOST = "data.hexin.cn"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_HEADERS = {
    "Host": _HEXIN_HOST,
    "Referer": "https://data.hexin.cn/",
    "User-Agent": _UA,
}

# sgt(深股通)近期不可靠,可能出現量級異常(遠超合理"億元"淨買入範圍)的髒值;
# 超過此絕對值閾值一律視為異常丟棄。閾值本身是防禦性經驗值,非精確業務規則。
_SGT_MAX_ABS = 2000.0


def _to_float(value) -> float | None:
    """寬鬆轉 float;None/無法轉換/NaN 一律 None(NaN 用 f != f 判定,不額外 import math)。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _sgt_valid(value) -> float | None:
    """sgt 專用:在 _to_float 基礎上再做量級容錯(近期不可靠,可能 NaN/異常大)。"""
    f = _to_float(value)
    if f is None:
        return None
    if abs(f) > _SGT_MAX_ABS:
        return None
    return f


def _unwrap_payload(resp) -> dict:
    """防禦性剝離外層包裹:hexin 回應可能是 {"data": {...}} 或再套一層
    {"data": {"data": {...}}}——具體結構待實抓校準,逐層 .get() 兜底,拿不到就 {}。
    """
    if not isinstance(resp, dict):
        return {}
    layer = resp.get("data")
    if not isinstance(layer, dict):
        return {}
    inner = layer.get("data")
    if isinstance(inner, dict):
        return inner
    return layer


def _last_point(series) -> tuple[object, object]:
    """從分鐘序列取末值(當日最新累計淨買入)。序列元素可能是 [time, value] 或
    {"time":.., "value":..}(鍵名待實抓校準,防禦多種常見鍵名)。取不到返回 (None, None)。
    """
    if not isinstance(series, (list, tuple)) or not series:
        return None, None
    last = series[-1]
    if isinstance(last, (list, tuple)) and len(last) >= 2:
        return last[0], last[1]
    if isinstance(last, dict):
        t = last.get("time") or last.get("t") or last.get("x")
        v = last.get("value") or last.get("v") or last.get("y") or last.get("net")
        return t, v
    return None, None


class HexinNorthboundVendor(_NorthboundVendorBase):
    """北向資金(同花順 hexin):市場級,fetch 忽略 symbols。取當日分鐘序列末值組裝 1 條。"""

    name = "ths"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NorthboundItem]:
        data = market_get(
            _HEXIN_URL,
            host_key=_HEXIN_HOST,
            headers=_HEADERS,
            parse="json",
            retries=2,
            timeout=8,
            log_label="北向資金",
        )
        if not data:
            return []

        payload = _unwrap_payload(data)
        if not payload:
            return []

        hgt_time, hgt_raw = _last_point(payload.get("hgt"))
        sgt_time, sgt_raw = _last_point(payload.get("sgt"))
        hgt_net = _to_float(hgt_raw)
        sgt_net = _sgt_valid(sgt_raw)
        if hgt_net is None and sgt_net is None:
            return []

        total_net = hgt_net + sgt_net if (hgt_net is not None and sgt_net is not None) else None
        date = str(
            (data.get("date") if isinstance(data, dict) else None)
            or payload.get("date")
            or (config or {}).get("date")
            or ""
        )
        time_point = hgt_time if hgt_time is not None else sgt_time
        time_str = str(time_point) if time_point is not None else ""

        return [
            NorthboundItem(
                date=date,
                hgt_net=hgt_net,
                sgt_net=sgt_net,
                total_net=total_net,
                time=time_str,
            )
        ]
