"""跨市場股票程式碼值物件:一處歸一化,替代散落各處的 _to_market/字首邏輯。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    CN = "CN"
    HK = "HK"
    US = "US"
    TW = "TW"


_CN_RE = re.compile(r"^[036]\d{5}$")   # 6 位,0/3/6 開頭
_HK_RE = re.compile(r"^\d{5}$")        # 5 位數字
_TW_RE = re.compile(r"^\d{4}[A-Z]?$")  # 4 位數字或 4 位+字母 (如 2330, 2881A)
_US_RE = re.compile(r"^[A-Z.]{1,6}$")  # 1-6 位字母(含指數 .DJI)


def _detect_market(code: str) -> Market:
    c = code.strip().upper()
    if c.endswith(".TW") or c.endswith(".TWO"):
        return Market.TW
    if _CN_RE.match(c):
        return Market.CN
    if _HK_RE.match(c):
        return Market.HK
    if _TW_RE.match(c):
        return Market.TW
    if _US_RE.match(c):
        return Market.US
    # 兜底:6 位數字當 CN,其餘當 US
    return Market.CN if c.isdigit() and len(c) == 6 else Market.US


def _cn_exchange(code: str) -> str:
    """SH / SZ / BJ —— 與 src/core/cn_symbol.get_cn_exchange 規則一致。"""
    if code.startswith("920") or code.startswith(("83", "87", "88")):
        return "bj"
    if code.startswith(("5", "6")) or code.startswith("900"):
        return "sh"
    return "sz"


@dataclass(frozen=True)
class Symbol:
    market: Market
    code: str

    @classmethod
    def parse(cls, raw: str, market: str | None = None) -> "Symbol":
        code = raw.strip()
        u = code.upper()
        if u.endswith(".TW"):
            return cls(Market.TW, code[:-3])
        if u.endswith(".TWO"):
            return cls(Market.TW, code[:-4])
        if market:
            return cls(Market(market), code)
        return cls(_detect_market(code), code)

    def to_tencent(self) -> str:
        if self.market == Market.HK:
            return f"hk{self.code}"
        if self.market == Market.US:
            return f"us{self.code}"
        return _cn_exchange(self.code) + self.code

    def to_yfinance(self) -> str:
        if self.market == Market.HK:
            return f"{int(self.code):04d}.HK" if self.code.isdigit() else f"{self.code}.HK"
        if self.market == Market.TW:
            return f"{self.code}.TW" if "." not in self.code else self.code
        return self.code  # US 直接用;CN 由 vendor.supports_markets 攔截,不會走到這

    def to_eastmoney_secid(self) -> str:
        if self.market == Market.HK:
            return f"116.{self.code}"
        if self.market == Market.US:
            return f"105.{self.code}"
        return f"{'1' if _cn_exchange(self.code) == 'sh' else '0'}.{self.code}"
