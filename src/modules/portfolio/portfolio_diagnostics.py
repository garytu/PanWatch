"""組合診斷(Phase 4):只讀分析模擬交易持倉的集中度 / 分佈 / 風險。

對標 PortfolioPilot 的「只讀不下單」診斷 —— 純讀取持倉,**絕不下單**,只產出診斷與提示。
純函式 diagnose_positions 可單測;diagnose_paper_portfolio 讀 DB。
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import PaperTradingPosition

logger = logging.getLogger(__name__)

# 風險閾值(可後續配置化)
MAX_SINGLE_WEIGHT = 0.40   # 單倉佔比上限
HIGH_HHI = 0.50            # HHI 集中度高線
MAX_MARKET_WEIGHT = 0.70   # 單市場佔比上限
MIN_POSITIONS = 3          # 最少分散持倉數


def herfindahl(values: list[float]) -> float:
    """HHI 集中度 = Σ(w_i)²(w 為歸一化權重)。範圍 [1/n, 1],越大越集中。"""
    total = sum(values)
    if total <= 0:
        return 0.0
    return sum((v / total) ** 2 for v in values)


def diagnose_positions(positions: list[dict]) -> dict:
    """純函式診斷。

    positions: [{symbol, market, strategy_code, market_value, unrealized_pnl}]
    """
    if not positions:
        return {
            "position_count": 0,
            "total_market_value": 0.0,
            "hhi": 0.0,
            "max_weight": 0.0,
            "by_market": {},
            "by_strategy": {},
            "total_unrealized_pnl": 0.0,
            "alerts": [],
        }

    values = [max(0.0, float(p.get("market_value") or 0.0)) for p in positions]
    total = sum(values)
    hhi = herfindahl(values)
    max_w = (max(values) / total) if total > 0 else 0.0

    by_market: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    for p, v in zip(positions, values):
        m = p.get("market") or "?"
        s = p.get("strategy_code") or "?"
        by_market[m] = by_market.get(m, 0.0) + v
        by_strategy[s] = by_strategy.get(s, 0.0) + v

    upnl = sum(float(p.get("unrealized_pnl") or 0.0) for p in positions)

    alerts: list[str] = []
    if max_w >= MAX_SINGLE_WEIGHT:
        alerts.append(f"單倉集中度過高:最大持倉佔 {max_w * 100:.0f}%")
    if hhi >= HIGH_HHI:
        alerts.append(f"組合高度集中(HHI={hhi:.2f})")
    if len(positions) < MIN_POSITIONS and total > 0:
        alerts.append(f"持倉數過少({len(positions)}),分散不足")
    if total > 0:
        for m, v in by_market.items():
            if v / total >= MAX_MARKET_WEIGHT:
                alerts.append(f"{m} 市場佔比過高({v / total * 100:.0f}%)")

    return {
        "position_count": len(positions),
        "total_market_value": round(total, 2),
        "hhi": round(hhi, 4),
        "max_weight": round(max_w, 4),
        "by_market": {k: round(v, 2) for k, v in by_market.items()},
        "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
        "total_unrealized_pnl": round(upnl, 2),
        "alerts": alerts,
    }


def diagnose_paper_portfolio() -> dict:
    """讀模擬交易 open 持倉 → 組合診斷(只讀)。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        positions: list[dict] = []
        for p in rows:
            price = p.current_price or p.entry_price or 0.0
            market_value = float(price) * int(p.quantity or 0)
            positions.append(
                {
                    "symbol": p.stock_symbol,
                    "market": p.stock_market,
                    "strategy_code": p.strategy_code or "",
                    "market_value": market_value,
                    "unrealized_pnl": float(p.unrealized_pnl or 0.0),
                }
            )
        return diagnose_positions(positions)
    finally:
        db.close()
