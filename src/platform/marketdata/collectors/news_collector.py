"""新聞資料結構 + 聚合採集器薄 shim。

實際抓取(雪球個股新聞 / 東財個股新聞搜尋 / 東財公告)已收口進 marketdata 包
(packages/marketdata),本檔案只保留消費方仍在用的 NewsItem 資料結構，以及
一個轉發到包的 NewsCollector shim，對消費方零改動。
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    """新聞資料結構"""
    source: str           # "xueqiu" / "eastmoney_news" / "eastmoney"
    external_id: str      # 來源側唯一ID
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)  # 關聯股票程式碼
    importance: int = 0   # 0-3 重要性
    url: str = ""         # 原文連結


class NewsCollector:
    """聚合新聞採集器 —— 薄 shim，實際抓取/聚合/去重邏輯已收口進 marketdata 包。"""

    @classmethod
    def from_database(cls) -> "NewsCollector":
        """配置現由包內 DbConfigProvider 按需讀 DataSource 表，這裡直接返回例項。"""
        return cls()

    async def fetch_all(
        self,
        symbols: list[str] | None = None,
        since_hours: int = 2,
        symbol_names: dict[str, str] | None = None,
    ) -> list[NewsItem]:
        """
        聚合所有已啟用新聞資料來源的新聞（聚合/去重/排序均在 marketdata 包內完成）。

        Args:
            symbols: 股票程式碼列表
            since_hours: 獲取最近 N 小時的新聞（公告類源的視窗由包內自動放寬）
            symbol_names: 股票程式碼到名稱的對映（可選，eastmoney_news 用名稱搜尋效果更好）

        Returns:
            按時間倒序排列的新聞列表
        """
        from src.platform.marketdata.marketdata_client import md_news

        return await asyncio.to_thread(md_news, symbols or [], since_hours, symbol_names)
