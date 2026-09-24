"""包內各資料型別的合法 vendor 權威(唯一真相源)。

`MarketData.__init__` 用 `build_vendors(datatype)` 構建各 Engine 的 `vendors={}`;
`PACKAGE_VENDORS_BY_TYPE` 由同一份 `VENDOR_CLASSES_BY_TYPE` 派生 vendor 名集合。
兩者共享同一份類對映,不會出現"改了 Engine 忘了改權威表"的漂移。

宿主(PanWatch `DataSource` 表)據此判定某行 `(type, provider)` 是否為孤兒:
`legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 內該 type 的 provider 集合`。
discovery/index 是市場級、非 symbol 模型,不進 Engine/不進 DataSource,故不出現在此表。
"""

from __future__ import annotations

from marketdata.vendors.capital_flow import EastmoneyCapitalFlowVendor, SinaCapitalFlowVendor
from marketdata.vendors.eastmoney import EastmoneyQuoteVendor
from marketdata.vendors.events import EventsVendor
from marketdata.vendors.external_feed import ExternalKlineVendor, ExternalQuoteVendor
from marketdata.vendors.finmind import (
    FinMindCapitalFlowVendor,
    FinMindDividendVendor,
    FinMindFundamentalsVendor,
    FinMindKlineVendor,
    FinMindMarginVendor,
    FinMindNewsVendor,
)
from marketdata.vendors.fundamentals import EastmoneyFundamentalsVendor, TencentFundamentalsVendor
from marketdata.vendors.flash_news import (
    ClsFlashNewsVendor,
    EastmoneyFlashNewsVendor,
    SinaFlashNewsVendor,
)
from marketdata.vendors.kline import (
    EastmoneyKlineVendor,
    StooqKlineVendor,
    TencentKlineVendor,
    YahooKlineVendor,
)
from marketdata.vendors.market_flow import (
    EastmoneyDividendVendor,
    EastmoneyDragonTigerVendor,
    EastmoneyMarginVendor,
    EastmoneyShareholdersVendor,
)
from marketdata.vendors.news import (
    EastmoneyAnnNewsVendor,
    EastmoneyStockNewsVendor,
    XueqiuNewsVendor,
)
from marketdata.vendors.northbound import HexinNorthboundVendor
from marketdata.vendors.sina import SinaQuoteVendor
from marketdata.vendors.tencent import TencentQuoteVendor
from marketdata.vendors.yfinance import YFinanceQuoteVendor

# 各資料型別 → {vendor name: vendor 類}。注意:vendor 的 import 本身是廉價的
# (可選三方依賴如 yfinance 均在 fetch() 內部惰性 import),模組級匯入不會引入重依賴。
VENDOR_CLASSES_BY_TYPE: dict[str, dict[str, type]] = {
    "quote": {
        "tencent": TencentQuoteVendor,
        "sina": SinaQuoteVendor,
        "eastmoney": EastmoneyQuoteVendor,
        "yfinance": YFinanceQuoteVendor,
        "external_quote": ExternalQuoteVendor,
    },
    "kline": {
        "tencent": TencentKlineVendor,
        "stooq": StooqKlineVendor,
        "eastmoney": EastmoneyKlineVendor,
        "yahoo": YahooKlineVendor,
        "finmind": FinMindKlineVendor,
    },
    "intraday_kline": {
        "external_kline": ExternalKlineVendor,
    },
    "capital_flow": {
        "eastmoney": EastmoneyCapitalFlowVendor,
        "sina": SinaCapitalFlowVendor,
        "finmind": FinMindCapitalFlowVendor,
    },
    "events": {
        "eastmoney": EventsVendor,
    },
    "fundamentals": {
        "tencent": TencentFundamentalsVendor,
        "eastmoney": EastmoneyFundamentalsVendor,
        "finmind": FinMindFundamentalsVendor,
    },
    "flash_news": {
        "cls": ClsFlashNewsVendor,
        "sina": SinaFlashNewsVendor,
        "eastmoney": EastmoneyFlashNewsVendor,
    },
    "news": {
        "xueqiu": XueqiuNewsVendor,
        "eastmoney_news": EastmoneyStockNewsVendor,
        "eastmoney": EastmoneyAnnNewsVendor,
        "finmind": FinMindNewsVendor,
    },
    "dragon_tiger": {
        "eastmoney": EastmoneyDragonTigerVendor,
    },
    "margin": {
        "eastmoney": EastmoneyMarginVendor,
        "finmind": FinMindMarginVendor,
    },
    "shareholders": {
        "eastmoney": EastmoneyShareholdersVendor,
    },
    "dividend": {
        "eastmoney": EastmoneyDividendVendor,
        "finmind": FinMindDividendVendor,
    },
    "northbound": {
        "ths": HexinNorthboundVendor,
    },
}

# 各資料型別的合法 vendor 名集合(凍結,防止呼叫方誤改)。
PACKAGE_VENDORS_BY_TYPE: dict[str, frozenset[str]] = {
    datatype: frozenset(classes.keys()) for datatype, classes in VENDOR_CLASSES_BY_TYPE.items()
}


def build_vendors(datatype: str) -> dict[str, object]:
    """例項化某資料型別的全部 vendor,供 Engine 注入。未知 datatype 返回空字典。"""
    return {name: cls() for name, cls in VENDOR_CLASSES_BY_TYPE.get(datatype, {}).items()}
