"""Vendor 抽象:每個 vendor 只負責"一家源怎麼抓 + 解析成標準型別",內部無 fallback。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from marketdata.symbol import Symbol


class Vendor(ABC):
    #: 註冊名,與 SourceConfig.vendor / DataSource.provider 對齊
    name: str = ""
    #: 支援的市場集合(空集=全部);Engine 會按市場過濾
    supports_markets: set[str] = set()

    @abstractmethod
    def fetch(self, symbols: list[Symbol], config: dict) -> list:
        """抓取並解析。失敗應拋異常(Engine 捕獲後轉移),空結果返回 []。"""
        ...


class QuoteVendor(Vendor):
    """報價 vendor:fetch 返回 list[Quote]。"""

    pass


class KlineVendor(Vendor):
    """K 線 vendor:fetch 返回 list[Bar]。單 symbol。"""

    pass


class CapitalFlowVendor(Vendor):
    """資金流向 vendor:fetch 返回 list[CapitalFlow]。單 symbol。"""

    pass


class EventsVendor(Vendor):
    """事件 vendor:fetch 返回 list[EventItem]。批次(多 symbol)。"""

    pass


class FlashNewsVendor(Vendor):
    """快訊 vendor:fetch 返回 list[FlashNews]。市場級,symbols 可空。"""

    pass


class NewsVendor(Vendor):
    """新聞 vendor:返回 list[NewsArticle],按 symbol。"""

    pass


class FundamentalsVendor(Vendor):
    """基本面/財務 vendor:fetch 返回 list[Fundamentals]。按 symbol(批次)。"""

    pass


class DragonTigerVendor(Vendor):
    """龍虎榜 vendor:fetch 返回 list[DragonTigerItem]。市場級(symbols 恆空),按 date 過濾。"""

    pass


class MarginVendor(Vendor):
    """融資融券 vendor:fetch 返回 list[MarginItem]。按 symbol(逐只取最新快照)。"""

    pass


class ShareholdersVendor(Vendor):
    """股東戶數 vendor:fetch 返回 list[ShareholderItem]。按 symbol(逐只取最新一期)。"""

    pass


class DividendVendor(Vendor):
    """分紅 vendor:fetch 返回 list[DividendItem]。按 symbol(逐只返回全部歷史)。"""

    pass


class NorthboundVendor(Vendor):
    """北向資金 vendor:fetch 返回 list[NorthboundItem]。市場級(symbols 可空)。"""

    pass
