"""股票外部連結生成工具：根據股票程式碼、市場和使用者選擇的平臺生成行情頁 URL。

全域性設定 key: stock_link_platform (預設 xueqiu)
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

# 支援的平臺 {code: 中文名}
PLATFORMS = {
    "xueqiu": "雪球",
}

DEFAULT_PLATFORM = "xueqiu"
SETTING_KEY = "stock_link_platform"


def get_platform() -> str:
    """從 AppSettings 讀取當前配置的平臺程式碼。"""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
        return (row.value if row and row.value else DEFAULT_PLATFORM)
    finally:
        db.close()


def stock_url(symbol: str, market: str, platform: str = "") -> str:
    """生成股票行情頁 URL。

    Args:
        symbol: 股票程式碼，如 "002837", "AAPL", "00883"
        market: 市場程式碼，如 "CN", "US", "HK"
        platform: 平臺程式碼，為空時從全域性設定讀取
    """
    if not platform:
        platform = get_platform()

    m = market.upper()

    if platform == "xueqiu":
        return _xueqiu_url(symbol, m)

    # 兜底
    return _xueqiu_url(symbol, m)


def stock_link_markdown(symbol: str, market: str, platform: str = "") -> str:
    """生成 Markdown 格式的股票連結: [002837.CN](https://xueqiu.com/S/SZ002837)"""
    code = f"{symbol}.{market}"
    url = stock_url(symbol, market, platform)
    return f"[{code}]({url})"


# ---------------------------------------------------------------------------
# 各平臺 URL 生成
# ---------------------------------------------------------------------------

def _xueqiu_url(symbol: str, market: str) -> str:
    if market == "US":
        return f"https://xueqiu.com/S/{symbol}"
    if market == "HK":
        return f"https://xueqiu.com/S/{symbol}"
    # CN A股
    from src.platform.marketdata.cn_symbol import get_cn_prefix
    prefix = get_cn_prefix(symbol, upper=True)
    return f"https://xueqiu.com/S/{prefix}{symbol}"
