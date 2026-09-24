"""模擬交易跟單通知：建倉/平倉即時推送、盤前計劃、日終摘要。"""

from __future__ import annotations

import logging
from typing import Any

from src.platform.notifications.notifier import NotifierManager
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AppSettings,
    NotifyChannel,
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
    StrategySignalRun,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置讀取
# ---------------------------------------------------------------------------

_CONFIG_KEYS = {
    "pt_notify_enabled": "false",
    "pt_notify_channel_ids": "",
    "pt_notify_realtime": "true",
    "pt_notify_premarket": "true",
    "pt_notify_summary": "true",
}


def _load_config() -> dict[str, str]:
    """從 AppSettings 表讀取 pt_notify_* 配置。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(_CONFIG_KEYS.keys()))
            .all()
        )
        cfg = dict(_CONFIG_KEYS)  # defaults
        for r in rows:
            cfg[r.key] = r.value or _CONFIG_KEYS.get(r.key, "")
        return cfg
    finally:
        db.close()


def _is_enabled() -> bool:
    cfg = _load_config()
    return cfg.get("pt_notify_enabled", "").lower() == "true"


def _is_mode_enabled(mode_key: str) -> bool:
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return False
    return cfg.get(mode_key, "").lower() == "true"


# ---------------------------------------------------------------------------
# 管道構建
# ---------------------------------------------------------------------------

def _build_notifier() -> NotifierManager | None:
    """根據配置構建 NotifierManager，無可用管道時返回 None。"""
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return None

    db = SessionLocal()
    try:
        channel_ids_str = cfg.get("pt_notify_channel_ids", "").strip()
        if channel_ids_str:
            ids = [int(x.strip()) for x in channel_ids_str.split(",") if x.strip().isdigit()]
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.id.in_(ids), NotifyChannel.enabled.is_(True))
                .all()
            )
        else:
            # 未指定管道時使用預設管道
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.enabled.is_(True), NotifyChannel.is_default.is_(True))
                .all()
            )

        if not channels:
            logger.debug("[模擬交易通知] 無可用通知管道")
            return None

        mgr = NotifierManager()
        for ch in channels:
            mgr.add_channel(ch.type, ch.config or {})
        return mgr
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 訊息格式化
# ---------------------------------------------------------------------------

EXIT_REASON_LABELS = {
    "stop_loss": "停損",
    "target_price": "停利",
    "signal_reversal": "訊號反轉",
    "manual": "手動平倉",
}

STRATEGY_NAME_MAP = {
    "trend_follow": "趨勢延續",
    "macd_golden": "MACD金叉",
    "volume_breakout": "放量突破",
    "pullback": "回踩確認",
    "rebound": "超跌反彈",
    "watchlist_agent": "Agent建議",
    "market_scan": "市場掃描",
    "momentum": "動量策略",
}


def _strategy_label(code: str) -> str:
    """策略程式碼轉中文名稱。"""
    return STRATEGY_NAME_MAP.get(code, code)


def _stock_display(symbol: str, market: str, name: str = "") -> str:
    """生成帶連結的股票顯示文本，點選程式碼跳轉到行情頁。"""
    from src.modules.administration.stock_link import stock_link_markdown
    label = f"{name} " if name else ""
    return f"{label}({stock_link_markdown(symbol, market)})"


def _format_entry_message(pos: dict, sig: dict | None) -> tuple[str, str]:
    """格式化建倉通知，返回 (title, body)。pos/sig 為序列化後的 dict。"""
    name = pos.get("stock_name") or pos["stock_symbol"]
    title = f"【模擬交易建倉】{name}"

    # 賺賠比
    rr_str = ""
    entry_price = pos.get("entry_price", 0)
    stop_loss = pos.get("stop_loss", 0)
    target_price = pos.get("target_price", 0)
    if stop_loss and target_price and entry_price:
        risk = abs(entry_price - stop_loss)
        reward = abs(target_price - entry_price)
        if risk > 0:
            rr_str = f"\n賺賠比: {reward / risk:.1f}:1"

    score_str = ""
    strategy_code = pos.get("strategy_code", "")
    if sig:
        if sig.get("rank_score"):
            score_str = f" | 評分: {sig['rank_score']:.1f}"
        if sig.get("strategy_code"):
            strategy_code = sig["strategy_code"]

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    body = (
        f"股票: {stock_info}\n"
        f"方向: 買入\n"
        f"買入價: {entry_price:.2f} | 數量: {pos['quantity']} 股\n"
        f"停損價: {stop_loss:.2f} | 目標價: {target_price:.2f}\n"
        f"策略: {_strategy_label(strategy_code)}{score_str}"
        f"{rr_str}"
    )
    return title, body


def _format_exit_message(pos: dict, trade: dict) -> tuple[str, str]:
    """格式化平倉通知，返回 (title, body)。pos/trade 為序列化後的 dict。"""
    name = pos.get("stock_name") or pos["stock_symbol"]
    pnl = trade["pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"【模擬交易平倉】{name} {pnl_sign}{pnl:.2f}"

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    reason = EXIT_REASON_LABELS.get(trade["exit_reason"], trade["exit_reason"])
    body = (
        f"股票: {stock_info}\n"
        f"平倉原因: {reason}\n"
        f"買入價: {trade['entry_price']:.2f} → 賣出價: {trade['exit_price']:.2f}\n"
        f"損益: {pnl_sign}{pnl:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%) | 持倉: {trade['holding_days']}天"
    )
    return title, body


def _dedup_signals(signals: list[StrategySignalRun]) -> list[tuple[StrategySignalRun, int]]:
    """按 (stock_symbol, stock_market) 去重，保留 rank_score 最高的訊號。
    返回 [(signal, strategy_count), ...]，已按 rank_score desc 排序。
    """
    seen: dict[tuple[str, str], tuple[StrategySignalRun, int]] = {}
    for sig in signals:
        key = (sig.stock_symbol, sig.stock_market)
        if key not in seen:
            seen[key] = (sig, 1)
        else:
            _, count = seen[key]
            seen[key] = (seen[key][0], count + 1)
    # 已按 rank_score desc 查詢，保留首次出現的順序即可
    return list(seen.values())


def _format_premarket_plan(signals: list[StrategySignalRun], account: PaperTradingAccount) -> tuple[str, str]:
    """格式化盤前計劃，返回 (title, body)。訊號會自動去重。"""
    title = "【模擬交易盤前計劃】"
    if not signals:
        return title, "今日無候選股票"

    deduped = _dedup_signals(signals)

    lines = [f"可用資金: {account.current_capital:,.2f}\n"]
    lines.append("今日候選:")
    for i, (sig, strat_count) in enumerate(deduped, 1):
        name = sig.stock_name or sig.stock_symbol
        from src.modules.administration.stock_link import stock_link_markdown
        link = stock_link_markdown(sig.stock_symbol, sig.stock_market)
        entry_range = ""
        if sig.entry_low and sig.entry_high:
            entry_range = f" 入場區間: {sig.entry_low:.2f}-{sig.entry_high:.2f}"
        score_str = f" 評分:{sig.rank_score:.1f}" if sig.rank_score else ""
        strat_label = _strategy_label(sig.strategy_code)
        if strat_count > 1:
            strat_label = f"{strat_label} 等{strat_count}個策略"
        lines.append(f"{i}. {name} ({link}){entry_range}{score_str} [{strat_label}]")

    return title, "\n".join(lines)


def _format_daily_summary(
    trades: list[PaperTradingTrade],
    positions: list[PaperTradingPosition],
    account: PaperTradingAccount,
) -> tuple[str, str]:
    """格式化日終摘要，返回 (title, body)。"""
    # 總資產
    positions_value = sum((p.current_price or p.entry_price) * p.quantity for p in positions)
    total_equity = account.current_capital + positions_value
    unrealized = sum(p.unrealized_pnl or 0 for p in positions)

    title = "【模擬交易日終摘要】"
    lines = [f"總資產: {total_equity:,.2f}"]

    # 當日平倉
    if trades:
        day_pnl = sum(t.pnl for t in trades)
        pnl_sign = "+" if day_pnl >= 0 else ""
        lines.append(f"\n當日平倉 {len(trades)} 筆, 損益: {pnl_sign}{day_pnl:,.2f}")
        for t in trades:
            s = "+" if t.pnl >= 0 else ""
            reason = EXIT_REASON_LABELS.get(t.exit_reason, t.exit_reason)
            lines.append(f"  · {t.stock_name or t.stock_symbol}: {s}{t.pnl:,.2f} ({s}{t.pnl_pct:.2f}%) [{reason}]")
    else:
        lines.append("\n當日無平倉操作")

    # 持倉未實現獲利
    if positions:
        u_sign = "+" if unrealized >= 0 else ""
        lines.append(f"\n持倉中 {len(positions)} 只, 未實現損益: {u_sign}{unrealized:,.2f}")
        for p in positions:
            pnl = p.unrealized_pnl or 0
            s = "+" if pnl >= 0 else ""
            lines.append(f"  · {p.stock_name or p.stock_symbol}: {s}{pnl:,.2f}")
    else:
        lines.append("\n當前無持倉")

    lines.append(f"\n可用資金: {account.current_capital:,.2f}")
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# 觸發函式
# ---------------------------------------------------------------------------

async def notify_entry(pos: dict, sig: dict | None) -> None:
    """建倉通知（非同步，失敗僅日誌）。pos/sig 為序列化後的 dict。"""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_entry_message(pos, sig)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模擬交易通知] 建倉通知傳送失敗")


async def notify_exit(pos: dict, trade: dict) -> None:
    """平倉通知（非同步，失敗僅日誌）。pos/trade 為序列化後的 dict。"""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_exit_message(pos, trade)
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模擬交易通知] 平倉通知傳送失敗")


async def send_premarket_plan() -> None:
    """盤前計劃通知。"""
    try:
        if not _is_mode_enabled("pt_notify_premarket"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            # 按投資比例排除不投入（比例為 0）的市場
            from src.modules.paper_trading.paper_trading_engine import ALL_MARKETS, market_allocations_or_default
            alloc = market_allocations_or_default(account)
            excluded = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
            query = (
                db.query(StrategySignalRun)
                .filter(
                    StrategySignalRun.status == "active",
                    StrategySignalRun.action.in_(["buy", "add"]),
                    StrategySignalRun.entry_low.isnot(None),
                    StrategySignalRun.entry_high.isnot(None),
                )
            )
            if excluded:
                query = query.filter(StrategySignalRun.stock_market.notin_(excluded))
            signals = query.order_by(StrategySignalRun.rank_score.desc()).all()

            title, body = _format_premarket_plan(signals, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模擬交易通知] 盤前計劃傳送失敗")


async def send_daily_summary() -> None:
    """日終摘要通知。"""
    try:
        if not _is_mode_enabled("pt_notify_summary"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

            # 當日已平倉
            trades = (
                db.query(PaperTradingTrade)
                .filter(PaperTradingTrade.closed_at >= today_start)
                .order_by(PaperTradingTrade.closed_at.desc())
                .all()
            )

            # 持倉中
            positions = (
                db.query(PaperTradingPosition)
                .filter(PaperTradingPosition.status == "open")
                .all()
            )

            title, body = _format_daily_summary(trades, positions, account)
            await mgr.notify(title, body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模擬交易通知] 日終摘要傳送失敗")


async def send_test_notification() -> dict:
    """傳送測試通知，返回結果。"""
    mgr = _build_notifier()
    if not mgr:
        return {"success": False, "error": "通知未啟用或無可用管道"}
    result = await mgr.notify_with_result(
        "【模擬交易測試】",
        "這是一條測試通知，確認通知管道配置正常。",
    )
    return result
