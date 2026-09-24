"""marketdata 異常型別。"""


class MarketDataError(Exception):
    """本包所有異常的基類。"""


class VendorError(MarketDataError):
    """單個 vendor 抓取失敗(Engine 捕獲後轉移到下一個源)。"""
