"""回測績效指標 —— 純函式,僅依賴標準庫。

約定:
- equity_curve: list[float],逐(交易日)淨值序列(含未實現損益),首元素為期初資金。
- trade_pnls: list[float],每筆已平倉交易的淨損益(已扣成本)。
"""

from __future__ import annotations

import math
import statistics

TRADING_DAYS_PER_YEAR = 252


def daily_returns(equity_curve: list[float]) -> list[float]:
    """由淨值序列推日報酬率。"""
    out: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev and prev > 0:
            out.append(equity_curve[i] / prev - 1.0)
        else:
            out.append(0.0)
    return out


def total_return(equity_curve: list[float]) -> float:
    if len(equity_curve) < 2 or not equity_curve[0]:
        return 0.0
    return equity_curve[-1] / equity_curve[0] - 1.0


def annualized_return(
    equity_curve: list[float], periods_per_year: int = TRADING_DAYS_PER_YEAR
) -> float:
    n = len(equity_curve) - 1
    if n <= 0 or not equity_curve[0] or equity_curve[0] <= 0 or equity_curve[-1] <= 0:
        return 0.0
    growth = equity_curve[-1] / equity_curve[0]
    return growth ** (periods_per_year / n) - 1.0


def max_drawdown(equity_curve: list[float]) -> float:
    """最大回檔(正數,如 0.23 表示 -23%)。"""
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def sharpe(
    returns: list[float],
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """年化夏普(樣本標準差)。returns 為週期報酬率序列。"""
    if len(returns) < 2:
        return 0.0
    rf_per_period = risk_free / periods_per_year
    excess = [r - rf_per_period for r in returns]
    mean = statistics.fmean(excess)
    sd = statistics.stdev(excess)
    if sd == 0:
        return 0.0
    return mean / sd * math.sqrt(periods_per_year)


def win_rate(trade_pnls: list[float]) -> float:
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)


def profit_factor(trade_pnls: list[float]) -> float:
    """賺賠比 = 總盈利 / 總虧損(絕對值)。無虧損時返回 inf。"""
    gains = sum(p for p in trade_pnls if p > 0)
    losses = -sum(p for p in trade_pnls if p < 0)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def avg_win_loss(trade_pnls: list[float]) -> tuple[float, float]:
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]
    avg_w = statistics.fmean(wins) if wins else 0.0
    avg_l = statistics.fmean(losses) if losses else 0.0
    return avg_w, avg_l


def summarize(equity_curve: list[float], trade_pnls: list[float]) -> dict:
    """彙總所有績效指標為一個 dict。"""
    rets = daily_returns(equity_curve)
    avg_w, avg_l = avg_win_loss(trade_pnls)
    return {
        "total_return": round(total_return(equity_curve), 6),
        "annualized_return": round(annualized_return(equity_curve), 6),
        "max_drawdown": round(max_drawdown(equity_curve), 6),
        "sharpe": round(sharpe(rets), 4),
        "trades": len(trade_pnls),
        "win_rate": round(win_rate(trade_pnls), 4),
        "profit_factor": round(profit_factor(trade_pnls), 4),
        "avg_win": round(avg_w, 4),
        "avg_loss": round(avg_l, 4),
        "total_pnl": round(sum(trade_pnls), 4),
    }
