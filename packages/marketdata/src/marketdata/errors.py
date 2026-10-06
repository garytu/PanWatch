"""marketdata 異常型別。"""


class MarketDataError(Exception):
    """本包所有異常的基類。"""


class VendorError(MarketDataError):
    """單個 vendor 抓取失敗(Engine 捕獲後轉移到下一個源)。"""


class TwmdReadError(VendorError):
    """A failed twmd query read; HTTP failures remain distinct from empty data."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason_code: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.reason_code = reason_code
