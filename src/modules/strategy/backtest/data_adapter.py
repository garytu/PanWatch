"""回測資料適配:KlineCollector → PriceBar,交易日曆對齊。

- PriceBar 定義在本模組頂層,且 **不在頂層 import KlineCollector**(延遲匯入),
  使回測核心與單測不被 httpx/網路庫耦合,可離線執行。
- KlineCollector 返回的已是前復權(qfq)日線,停牌日天然無 bar,交易日曆 = 實際 bar 序列。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBar:
    """單根日 K(前復權)。"""

    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def from_klines(klines) -> list[PriceBar]:
    """KlineData 列表 → 按日期升序的 PriceBar 列表。"""
    out: list[PriceBar] = []
    for k in klines or []:
        try:
            out.append(
                PriceBar(
                    date=str(k.date)[:10],
                    open=float(k.open),
                    high=float(k.high),
                    low=float(k.low),
                    close=float(k.close),
                    volume=float(k.volume or 0),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def load_price_history(symbol: str, market, days: int = 250) -> list[PriceBar]:
    """走 KlineCollector 拉歷史(延遲匯入,避免頂層耦合網路庫)。"""
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode

    try:
        mc = market if isinstance(market, MarketCode) else MarketCode(str(market).upper())
    except Exception:
        mc = MarketCode.CN
    try:
        klines = KlineCollector(mc).get_klines(symbol, days=days)
    except Exception as e:
        logger.warning(f"[回測] 拉取 {symbol} K線失敗: {e}")
        return []
    return from_klines(klines)


def first_index_after(bars: list[PriceBar], date: str) -> int | None:
    """返回第一個 date 嚴格大於給定日期的 bar 下標(下一交易日,防 look-ahead)。"""
    for i, b in enumerate(bars):
        if b.date > date:
            return i
    return None
