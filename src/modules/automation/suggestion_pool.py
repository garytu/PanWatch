"""建議池管理 - 彙總各 Agent 建議"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from datetime import timezone
from sqlalchemy import and_, func, or_

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import StockSuggestion
from src.platform.scheduling.timezone import utc_now, to_iso_with_tz
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def _norm_text(s: str) -> str:
    return " ".join((s or "").strip().split())


def _dedupe_window_minutes(agent_name: str) -> int:
    # Default: keep the suggestion list stable and avoid repeated rows.
    # Intraday runs frequently; other agents run a few times a day.
    if agent_name == "intraday_monitor":
        return 30
    if agent_name == "news_digest":
        return 60
    return 180


# Agent 有效期配置（小時）
AGENT_EXPIRY_HOURS = {
    "premarket_outlook": 12,  # 盤前建議當日有效（約12小時）
    "intraday_monitor": 6,  # 盤中建議6小時有效
    "daily_report": 16,  # 盤後建議隔夜有效（到次日開盤，約16小時）
    "news_digest": 12,  # 新聞速遞建議半天有效
}

# Agent 中文名稱對映
AGENT_LABELS = {
    "premarket_outlook": "盤前分析",
    "intraday_monitor": "盤中監測",
    "daily_report": "收盤覆盤",
    "news_digest": "新聞速遞",
}

def save_suggestion(
    stock_symbol: str,
    stock_name: str,
    action: str,
    action_label: str,
    agent_name: str,
    signal: str = "",
    reason: str = "",
    agent_label: str = "",
    expires_hours: Optional[int] = None,
    prompt_context: str = "",
    ai_response: str = "",
    stock_market: str = "CN",
    meta: dict | None = None,
) -> bool:
    """
    儲存 Agent 建議到建議池

    Args:
        stock_symbol: 股票程式碼
        stock_name: 股票名稱
        action: 操作型別 (buy/add/reduce/sell/hold/watch/alert/avoid)
        action_label: 操作中文標籤
        agent_name: Agent 名稱
        signal: 訊號描述
        reason: 建議理由
        agent_label: Agent 中文名稱（可選，自動推斷）
        expires_hours: 過期時間（小時），不指定則使用預設配置
        prompt_context: Prompt 上下文摘要
        ai_response: AI 原始回應

    Returns:
        是否儲存成功
    """
    db = SessionLocal()
    try:
        market = (stock_market or "CN").strip().upper() or "CN"

        # 計算過期時間（使用 UTC）
        if expires_hours is None:
            expires_hours = AGENT_EXPIRY_HOURS.get(agent_name, 8)

        now = utc_now()
        expires_at = now + timedelta(hours=expires_hours)

        # Agent 標籤
        if not agent_label:
            agent_label = AGENT_LABELS.get(agent_name, agent_name)

        # Dedupe: if the latest suggestion from the same agent is essentially the same,
        # do not create a new row. This prevents "AI 建議反覆" in the UI.
        try:
            latest = (
                db.query(StockSuggestion)
                .filter(
                    StockSuggestion.stock_symbol == stock_symbol,
                    StockSuggestion.stock_market == market,
                    StockSuggestion.agent_name == agent_name,
                )
                .order_by(StockSuggestion.created_at.desc(), StockSuggestion.id.desc())
                .first()
            )

            if latest and latest.created_at:
                latest_created = latest.created_at
                if latest_created.tzinfo is None:
                    latest_created = latest_created.replace(tzinfo=timezone.utc)

                window = timedelta(minutes=_dedupe_window_minutes(agent_name))
                same_key = (
                    _norm_text(latest.action) == _norm_text(action)
                    and _norm_text(latest.action_label) == _norm_text(action_label)
                    and _norm_text(latest.signal or "") == _norm_text(signal)
                )

                if same_key and (now - latest_created) <= window:
                    # Extend expiry (keep the first message to avoid churn).
                    if not latest.expires_at or latest.expires_at < expires_at:
                        latest.expires_at = expires_at
                    if not (latest.stock_name or "") and stock_name:
                        latest.stock_name = stock_name
                    db.commit()
                    logger.info(
                        f"建議去重: {stock_symbol} {action_label} (來源: {agent_label})"
                    )
                    return True

                # Stability: avoid flip-flopping to a less severe action within a short window.
                try:
                    action_rank = {
                        "alert": 4,
                        "avoid": 4,
                        "sell": 4,
                        "reduce": 3,
                        "buy": 2,
                        "add": 2,
                        "hold": 1,
                        "watch": 0,
                    }
                    old_r = action_rank.get((latest.action or "").strip(), 0)
                    new_r = action_rank.get((action or "").strip(), 0)
                    change_window = timedelta(
                        minutes=_dedupe_window_minutes(agent_name)
                    )
                    if (now - latest_created) <= change_window and new_r < old_r:
                        # Keep the previous (more severe) action; extend expiry.
                        if not latest.expires_at or latest.expires_at < expires_at:
                            latest.expires_at = expires_at
                        if not (latest.stock_name or "") and stock_name:
                            latest.stock_name = stock_name
                        db.commit()
                        logger.info(
                            f"建議穩定: {stock_symbol} 新建議降級({action_label})，保持上一條({latest.action_label})"
                        )
                        return True
                except Exception:
                    db.rollback()
        except Exception:
            # Best-effort only; never block saving.
            db.rollback()

        # 建立新建議
        suggestion = StockSuggestion(
            stock_symbol=stock_symbol,
            stock_market=market,
            stock_name=stock_name,
            action=action,
            action_label=action_label,
            signal=signal,
            reason=reason,
            agent_name=agent_name,
            agent_label=agent_label,
            expires_at=expires_at,
            prompt_context=prompt_context[:2000] if prompt_context else "",  # 限制長度
            ai_response=ai_response[:2000] if ai_response else "",  # 限制長度
            meta=to_jsonable(meta or {}),
        )
        db.add(suggestion)
        db.commit()

        logger.info(f"儲存建議: {stock_symbol} {action_label} (來源: {agent_label})")
        return True

    except Exception as e:
        logger.error(f"儲存建議失敗: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_suggestions_for_stock(
    stock_symbol: str,
    stock_market: str | None = None,
    include_expired: bool = False,
    limit: int = 10,
) -> list[dict]:
    """
    獲取某隻股票的建議列表

    Args:
        stock_symbol: 股票程式碼
        include_expired: 是否包含已過期建議
        limit: 返回數量限制

    Returns:
        建議列表，按時間倒序
    """
    db = SessionLocal()
    try:
        query = db.query(StockSuggestion).filter(StockSuggestion.stock_symbol == stock_symbol)
        if stock_market:
            query = query.filter(
                StockSuggestion.stock_market == (stock_market or "CN").strip().upper()
            )

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None)
                | (StockSuggestion.expires_at > now)
            )

        suggestions = (
            query.order_by(StockSuggestion.created_at.desc()).limit(limit).all()
        )

        return [_to_dict(s, now) for s in suggestions]

    finally:
        db.close()


def get_latest_suggestions(
    stock_symbols: Optional[list[str]] = None,
    stock_keys: Optional[list[tuple[str, str]]] = None,
    include_expired: bool = False,
) -> dict[str, dict]:
    """
    獲取所有股票的最新建議（每隻股票只返回最新的一條）

    Args:
        stock_symbols: 股票程式碼列表，None 表示所有
        include_expired: 是否包含已過期建議

    Returns:
        {symbol: suggestion_dict}
    """
    db = SessionLocal()
    try:
        subquery = (
            db.query(
                StockSuggestion.stock_symbol,
                StockSuggestion.stock_market,
                func.max(StockSuggestion.id).label("max_id"),
            )
            .group_by(StockSuggestion.stock_symbol, StockSuggestion.stock_market)
            .subquery()
        )

        query = db.query(StockSuggestion).join(
            subquery,
            and_(
                StockSuggestion.stock_symbol == subquery.c.stock_symbol,
                StockSuggestion.stock_market == subquery.c.stock_market,
                StockSuggestion.id == subquery.c.max_id,
            ),
        )

        if stock_keys:
            norm_keys = []
            for symbol, market in stock_keys:
                sym = (symbol or "").strip().upper()
                mkt = (market or "CN").strip().upper()
                if sym:
                    norm_keys.append((sym, mkt))
            if norm_keys:
                query = query.filter(
                    or_(
                        *[
                            and_(
                                StockSuggestion.stock_symbol == sym,
                                StockSuggestion.stock_market == mkt,
                            )
                            for sym, mkt in norm_keys
                        ]
                    )
                )
            else:
                return {}
        elif stock_symbols:
            query = query.filter(StockSuggestion.stock_symbol.in_(stock_symbols))

        now = utc_now()
        if not include_expired:
            query = query.filter(
                (StockSuggestion.expires_at == None)
                | (StockSuggestion.expires_at > now)
            )

        suggestions = query.all()

        result: dict[str, dict] = {}
        for s in suggestions:
            key = f"{(s.stock_market or 'CN').upper()}:{s.stock_symbol}"
            result[key] = _to_dict(s, now)
        return result

    finally:
        db.close()


def _to_dict(suggestion: StockSuggestion, now: Optional[datetime] = None) -> dict:
    """將 StockSuggestion 轉換為字典（時間使用 ISO 格式帶時區）"""
    if now is None:
        now = utc_now()

    is_expired = False
    if suggestion.expires_at:
        # 確保比較時都使用 UTC
        expires_utc = suggestion.expires_at
        if expires_utc.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            expires_utc = expires_utc.replace(tzinfo=timezone.utc)
        is_expired = expires_utc < now

    # 轉換時間為帶時區的 ISO 格式
    created_at_str = None
    if suggestion.created_at:
        created_at = suggestion.created_at
        if created_at.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_str = to_iso_with_tz(created_at)

    expires_at_str = None
    if suggestion.expires_at:
        expires_at = suggestion.expires_at
        if expires_at.tzinfo is None:
            from src.platform.scheduling.timezone import timezone

            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expires_at_str = to_iso_with_tz(expires_at)

    return {
        "id": suggestion.id,
        "stock_symbol": suggestion.stock_symbol,
        "stock_market": suggestion.stock_market or "CN",
        "stock_name": suggestion.stock_name,
        "action": suggestion.action,
        "action_label": suggestion.action_label,
        "signal": suggestion.signal,
        "reason": suggestion.reason,
        "agent_name": suggestion.agent_name,
        "agent_label": suggestion.agent_label,
        "created_at": created_at_str,
        "expires_at": expires_at_str,
        "is_expired": is_expired,
        "prompt_context": suggestion.prompt_context or "",
        "ai_response": suggestion.ai_response or "",
        "meta": suggestion.meta or {},
        "should_alert": (suggestion.action or "")
        in ("alert", "avoid", "sell", "reduce"),
    }


def cleanup_expired_suggestions(days: int = 7) -> int:
    """
    清理過期的建議記錄

    Args:
        days: 清理多少天前的記錄

    Returns:
        刪除的記錄數
    """
    db = SessionLocal()
    try:
        cutoff = utc_now() - timedelta(days=days)
        result = (
            db.query(StockSuggestion)
            .filter(StockSuggestion.created_at < cutoff)
            .delete()
        )
        db.commit()
        logger.info(f"清理了 {result} 條過期建議")
        return result
    except Exception as e:
        logger.error(f"清理過期建議失敗: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
