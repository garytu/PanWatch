"""策略層：訊號生成、後驗評估、調權與統計。"""

from __future__ import annotations

import logging
import hashlib
from datetime import date, datetime, timedelta
from math import isfinite, sqrt

from sqlalchemy import and_, case, exists, func, or_

from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.modules.strategy.entry_candidates import refresh_entry_candidates
from src.platform.persistence.json_safe import to_jsonable
from src.modules.strategy.strategy_catalog import (
    ensure_strategy_catalog,
    get_effective_weight_map,
    get_primary_horizon_sessions,
    get_strategy_profile_map,
    list_strategy_catalog,
)
from src.modules.strategy.factor_weights import get_factor_weights
from src.modules.strategy.calibration_gate import (
    apply_calibration_batch,
    capture_decision_snapshot,
    plan_calibration_batch,
)
from src.modules.strategy.ranking import (
    MISSING_FEATURE_POLICY,
    RANKER_VERSION_V1,
    RANKER_VERSION_V2,
    SCORE_VERSION_LEGACY,
    SCORE_VERSION_V2,
    TW_CONSTRAINT_POLICY_VERSION,
    TW_HIGH_RISK_SHARE,
    TW_INSTRUMENT_CAP,
    TW_SINGLE_STRATEGY_SHARE,
    canonical_instrument_id,
    group_instrument_rows,
    rank_map,
    representative_sort_key,
    stable_sigmoid_score,
)
from src.modules.strategy.outcome_kernel import (
    EVALUATOR_SIGNAL_FORWARD_V2,
    POPULATION_SIGNAL_FORWARD_V2,
)
from src.platform.scheduling.timezone import to_iso_with_tz, utc_now
from src.platform.marketdata.models import MarketCode, enabled_market_codes
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    EntryCandidate,
    MarketRegimeSnapshot,
    NewsCache,
    PortfolioConstraintLedger,
    PortfolioSelectionRun,
    PortfolioRiskSnapshot,
    RankingSnapshot,
    RankingSnapshotItem,
    StrategyFactorSnapshot,
    StrategyOutcome,
    StrategySignalRun,
    StrategyWeight,
    StrategyWeightHistory,
)

logger = logging.getLogger(__name__)


SOURCE_POOL_LABELS = {
    "watchlist": "關注池",
    "market_scan": "市場池",
    "mixed": "市場+關注",
}


def _compact_source_meta(meta: dict | None) -> dict:
    raw = meta if isinstance(meta, dict) else {}
    if not raw:
        return {}
    out: dict[str, object] = {}
    for key in (
        "trace_id",
        "context_quality_score",
        "trigger_price",
        "entry_low",
        "entry_high",
        "stop_loss",
        "target_price",
        "invalidation",
        "source",
    ):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            out[key] = text[:240]
        else:
            out[key] = value

    quote = raw.get("quote") if isinstance(raw.get("quote"), dict) else {}
    if quote:
        out["quote"] = {
            "current_price": _safe_float(quote.get("current_price")),
            "change_pct": _safe_float(quote.get("change_pct")),
            "turnover": _safe_float(quote.get("turnover")),
            "volume": _safe_float(quote.get("volume")),
        }
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
    if plan:
        compact_plan = {
            "entry_low": _safe_float(plan.get("entry_low")),
            "entry_high": _safe_float(plan.get("entry_high")),
            "stop_loss": _safe_float(plan.get("stop_loss")),
            "target_price": _safe_float(plan.get("target_price")),
            "invalidation": str(plan.get("invalidation") or "").strip()[:160],
        }
        out["plan"] = compact_plan
    return to_jsonable(out)


def _compact_signal_payload(payload: dict | None) -> dict:
    raw = payload if isinstance(payload, dict) else {}
    if not raw:
        return {}
    compact = {
        "source_meta": _compact_source_meta(
            raw.get("source_meta") if isinstance(raw.get("source_meta"), dict) else {}
        ),
        "score_breakdown": raw.get("score_breakdown") if isinstance(raw.get("score_breakdown"), dict) else {},
        "market_regime": raw.get("market_regime") if isinstance(raw.get("market_regime"), dict) else {},
        "cross_feature": raw.get("cross_feature") if isinstance(raw.get("cross_feature"), dict) else {},
        "news_metric": raw.get("news_metric") if isinstance(raw.get("news_metric"), dict) else {},
        "constrained": bool(raw.get("constrained")),
        "constraint_reasons": raw.get("constraint_reasons") if isinstance(raw.get("constraint_reasons"), list) else [],
    }
    return to_jsonable(compact)


def _normalize_action_view(
    *,
    action: str,
    action_label: str,
    is_holding: bool,
    rank_score: float,
    has_entry_plan: bool,
    score_version: str = SCORE_VERSION_LEGACY,
) -> tuple[str, str, float]:
    act = (action or "watch").strip().lower() or "watch"
    label = (action_label or "").strip()
    score = float(rank_score or 0.0)

    if is_holding:
        if act == "buy":
            act = "add"
            label = "準備加碼"
    else:
        if act == "add":
            act = "buy"
            label = "建倉"
        elif act == "hold":
            act = "watch"
            label = "觀望"
        if label in ("持有", "繼續持有"):
            act = "watch"
            label = "觀望"

    if act in ("watch", "hold"):
        if score_version != SCORE_VERSION_V2:
            score = min(score, 78.0 if is_holding else 65.0)
        if not label:
            label = "持有" if is_holding else "觀望"
    elif act == "buy":
        if not label:
            label = "建倉"
    elif act == "add":
        if not label:
            label = "準備加碼"
    else:
        if not label:
            label = "觀望"

    if act in ("buy", "add") and not has_entry_plan and score_version != SCORE_VERSION_V2:
        score = min(score, 66.0)

    return act, label, _clamp(score, 0.0, 100.0)

RISK_LEVEL_LABELS = {
    "low": "低風險",
    "medium": "中風險",
    "high": "高風險",
}

REGIME_LABELS = {
    "bullish": "多頭",
    "neutral": "震盪",
    "bearish": "空頭",
}

POSITIVE_EVENT_KEYWORDS = (
    "增持",
    "中標",
    "合作",
    "回購",
    "盈利",
    "訂單",
    "突破",
    "上調",
    "增長",
    "利好",
    "buyback",
    "contract",
    "beat",
    "upgrade",
)

NEGATIVE_EVENT_KEYWORDS = (
    "減持",
    "訴訟",
    "虧損",
    "下調",
    "違約",
    "處罰",
    "暴跌",
    "利空",
    "預警",
    "st",
    "downgrade",
    "miss",
    "fraud",
    "investigation",
)

MAX_UNHELD_ACTIVE_BY_MARKET = {
    "CN": 30,
    "HK": 20,
    "US": 20,
}

MAX_HIGH_RISK_RATIO_BY_MARKET = {
    "CN": 0.35,
    "HK": 0.32,
    "US": 0.30,
}

MAX_SINGLE_STRATEGY_SHARE = 0.42
MAX_SINGLE_STRATEGY_SHARE_BY_MARKET = {"TW": TW_SINGLE_STRATEGY_SHARE}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _finite_float(value) -> float | None:
    number = _safe_float(value)
    return number if number is not None and isfinite(number) else None


def _to_market(value: str | None) -> MarketCode:
    try:
        return MarketCode((value or "CN").strip().upper())
    except Exception:
        return MarketCode.CN


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            continue
    return None


def _pick_close_on_or_before(klines: list, target: date) -> float | None:
    if not klines:
        return None
    rows: list[tuple[date, float]] = []
    for k in klines:
        d = _parse_day(getattr(k, "date", None))
        c = getattr(k, "close", None)
        if d is None or c is None:
            continue
        try:
            rows.append((d, float(c)))
        except Exception:
            continue
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    for d, c in reversed(rows):
        if d <= target:
            return c
    return None


def _strategy_codes_for_candidate(row: EntryCandidate) -> list[str]:
    tags = [str(x).strip() for x in (row.strategy_tags or []) if str(x).strip()]
    codes: list[str] = []
    for tag in tags:
        codes.append(tag)
    if (row.candidate_source or "").strip() in ("market_scan", "mixed"):
        codes.append("market_scan")
    if not codes:
        codes.append("watchlist_agent")
    out: list[str] = []
    seen = set()
    for c in codes:
        if c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out


def _current_candidate_identity_filter():
    """Hide signals whose candidate ID now belongs to another instrument."""
    return or_(
        StrategySignalRun.source_candidate_id.is_(None),
        exists().where(and_(
            EntryCandidate.id == StrategySignalRun.source_candidate_id,
            EntryCandidate.snapshot_date == StrategySignalRun.snapshot_date,
            EntryCandidate.stock_symbol == StrategySignalRun.stock_symbol,
            EntryCandidate.stock_market == StrategySignalRun.stock_market,
        )),
    )


def _compute_rank_score(
    *,
    row: EntryCandidate,
    weight: float,
    risk_level: str,
) -> float:
    base = float(row.score or 0.0)
    quality_bonus = min(12.0, max(0.0, float(row.plan_quality or 0) / 12.0))
    source_bonus = (
        2.0 if (row.candidate_source or "") == "market_scan" else 1.2 if (row.candidate_source or "") == "mixed" else 0.0
    )
    status_penalty = -14.0 if (row.status or "inactive") != "active" else 0.0
    risk_penalty = 0.0
    if risk_level == "high":
        risk_penalty = -1.0
    rank = base * float(weight or 1.0) + quality_bonus + source_bonus + status_penalty + risk_penalty
    return _clamp(rank, 0.0, 100.0)


def _iso(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=utc_now().tzinfo)
    return to_iso_with_tz(dt)


def _source_label(value: str) -> str:
    return SOURCE_POOL_LABELS.get((value or "").strip(), value or "")


def _risk_label(value: str) -> str:
    return RISK_LEVEL_LABELS.get((value or "").strip(), value or "")


def _regime_label(value: str) -> str:
    return REGIME_LABELS.get((value or "").strip(), value or "")


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return sqrt(max(var, 0.0))


def _extract_candidate_quote_change_pct(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
    if quote.get("change_pct") is not None:
        return _safe_float(quote.get("change_pct"))
    source_meta = meta.get("source_meta") if isinstance(meta.get("source_meta"), dict) else {}
    source_quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return _safe_float(source_quote.get("change_pct"))


def _extract_candidate_volume_ratio(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    kline = meta.get("kline") if isinstance(meta.get("kline"), dict) else {}
    return _safe_float(kline.get("volume_ratio"))


def _extract_candidate_turnover(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
    if quote.get("turnover") is not None:
        return _safe_float(quote.get("turnover"))
    source_meta = meta.get("source_meta") if isinstance(meta.get("source_meta"), dict) else {}
    source_quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return _safe_float(source_quote.get("turnover"))


def _classify_market_regime(
    *,
    breadth_up_pct: float | None,
    avg_change_pct: float | None,
    active_ratio: float,
) -> tuple[str, float, float]:
    breadth_norm = (
        _clamp(((breadth_up_pct or 50.0) - 50.0) / 50.0, -1.0, 1.0)
        if breadth_up_pct is not None
        else 0.0
    )
    change_norm = (
        _clamp((avg_change_pct or 0.0) / 3.2, -1.0, 1.0)
        if avg_change_pct is not None
        else 0.0
    )
    active_norm = _clamp((active_ratio - 0.5) / 0.5, -1.0, 1.0)
    score = 0.45 * breadth_norm + 0.30 * change_norm + 0.25 * active_norm
    if score >= 0.20:
        regime = "bullish"
    elif score <= -0.20:
        regime = "bearish"
    else:
        regime = "neutral"
    confidence = _clamp(abs(score) * 1.45 + 0.15, 0.0, 1.0)
    return regime, round(score, 4), round(confidence, 4)


def _build_market_regime_rows(
    *,
    snapshot: str,
    candidates: list[EntryCandidate],
) -> dict[str, dict]:
    by_market: dict[str, list[EntryCandidate]] = {}
    for row in candidates:
        mkt = (row.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(mkt, []).append(row)

    out: dict[str, dict] = {}
    for market, rows in by_market.items():
        sample_size = len(rows)
        if sample_size <= 0:
            continue
        active_count = sum(1 for x in rows if (x.status or "inactive") == "active")
        active_ratio = active_count / sample_size if sample_size else 0.0
        changes = [x for x in (_extract_candidate_quote_change_pct(r) for r in rows) if x is not None]
        breadth_up_pct = (sum(1 for c in changes if c > 0) / len(changes) * 100.0) if changes else None
        avg_change_pct = (sum(changes) / len(changes)) if changes else None
        volatility_pct = _stdev(changes) if len(changes) >= 2 else None
        regime, regime_score, confidence = _classify_market_regime(
            breadth_up_pct=breadth_up_pct,
            avg_change_pct=avg_change_pct,
            active_ratio=active_ratio,
        )
        out[market] = {
            "snapshot_date": snapshot,
            "market": market,
            "regime": regime,
            "regime_label": _regime_label(regime),
            "regime_score": regime_score,
            "confidence": confidence,
            "breadth_up_pct": round(breadth_up_pct, 4) if breadth_up_pct is not None else None,
            "avg_change_pct": round(avg_change_pct, 4) if avg_change_pct is not None else None,
            "volatility_pct": round(volatility_pct, 4) if volatility_pct is not None else None,
            "active_ratio": round(active_ratio, 4),
            "sample_size": sample_size,
            "meta": {
                "active_signals": active_count,
                "total_signals": sample_size,
            },
        }
    return out


def _upsert_market_regime_snapshots(
    *,
    db,
    snapshot: str,
    candidates: list[EntryCandidate],
) -> dict[str, dict]:
    rows = _build_market_regime_rows(snapshot=snapshot, candidates=candidates)
    for market, data in rows.items():
        row = (
            db.query(MarketRegimeSnapshot)
            .filter(
                MarketRegimeSnapshot.snapshot_date == snapshot,
                MarketRegimeSnapshot.market == market,
            )
            .first()
        )
        if not row:
            row = MarketRegimeSnapshot(
                snapshot_date=snapshot,
                market=market,
            )
            db.add(row)
        row.regime = data["regime"]
        row.regime_score = float(data["regime_score"] or 0.0)
        row.confidence = float(data["confidence"] or 0.0)
        row.breadth_up_pct = data["breadth_up_pct"]
        row.avg_change_pct = data["avg_change_pct"]
        row.volatility_pct = data["volatility_pct"]
        row.active_ratio = float(data["active_ratio"] or 0.0)
        row.sample_size = int(data["sample_size"] or 0)
        row.meta = to_jsonable(data.get("meta") or {})
        row.updated_at = utc_now()
    return rows


def _load_news_metrics(
    *,
    db,
    candidates: list[EntryCandidate],
    lookback_hours: int = 72,
    max_rows: int = 5000,
) -> dict[str, dict]:
    if not candidates:
        return {}
    symbol_set = {
        (c.stock_symbol or "").strip().upper()
        for c in candidates
        if (c.stock_symbol or "").strip()
    }
    symbol_name_map = {
        (c.stock_symbol or "").strip().upper(): (c.stock_name or "").strip()
        for c in candidates
        if (c.stock_symbol or "").strip()
    }
    name_symbol_pairs = [
        (name, sym)
        for sym, name in symbol_name_map.items()
        if name and len(name) >= 2
    ]
    if not symbol_set:
        return {}

    cutoff = utc_now() - timedelta(hours=max(1, int(lookback_hours)))
    rows = (
        db.query(NewsCache)
        .filter(NewsCache.publish_time >= cutoff)
        .order_by(NewsCache.publish_time.desc())
        .limit(max(100, int(max_rows)))
        .all()
    )
    if not rows:
        return {}

    now = utc_now()
    metrics: dict[str, dict] = {}

    for n in rows:
        linked = set()
        for s in (n.symbols or []):
            x = str(s or "").strip().upper()
            if x and x in symbol_set:
                linked.add(x)
        if not linked:
            # Fallback: match by stock name mention in title/content to improve hit rate.
            text_for_match = f"{n.title or ''} {n.content or ''}"
            if text_for_match:
                for name, sym in name_symbol_pairs:
                    if name in text_for_match:
                        linked.add(sym)
            if not linked:
                continue
        title = str(n.title or "")
        content = str(n.content or "")
        text = f"{title} {content}".lower()
        event_bias = 0.0
        for kw in POSITIVE_EVENT_KEYWORDS:
            if kw.lower() in text:
                event_bias += 1.0
        for kw in NEGATIVE_EVENT_KEYWORDS:
            if kw.lower() in text:
                event_bias -= 1.2

        importance = int(n.importance or 0)
        published_at = n.publish_time
        if published_at is None:
            published_at = now
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=now.tzinfo)
        age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
        recency_weight = _clamp(1.0 - age_hours / 72.0, 0.05, 1.0)
        event_weight = recency_weight * (0.8 + 0.6 * importance)

        for sym in linked:
            m = metrics.setdefault(
                sym,
                {
                    "news_count": 0,
                    "high_importance_count": 0,
                    "importance_weighted": 0.0,
                    "event_bias_sum": 0.0,
                    "latest_age_hours": None,
                    "event_score": 0.0,
                    "event_bias": 0.0,
                },
            )
            m["news_count"] += 1
            if importance >= 2:
                m["high_importance_count"] += 1
            m["importance_weighted"] += float(event_weight)
            m["event_bias_sum"] += float(event_bias * recency_weight)
            if (m["latest_age_hours"] is None) or (age_hours < float(m["latest_age_hours"])):
                m["latest_age_hours"] = float(age_hours)

    for sym, m in metrics.items():
        cnt = int(m["news_count"] or 0)
        imp = float(m["importance_weighted"] or 0.0)
        hi_cnt = int(m["high_importance_count"] or 0)
        latest_age = float(m["latest_age_hours"] or 72.0)
        freshness_bonus = _clamp((24.0 - latest_age) / 8.0, -2.0, 3.0)
        event_score = _clamp(imp * 1.2 + hi_cnt * 1.4 + freshness_bonus, -6.0, 12.0)
        event_bias = _clamp(float(m["event_bias_sum"] or 0.0) / max(1.0, cnt), -3.0, 3.0)
        m["event_score"] = round(event_score, 4)
        m["event_bias"] = round(event_bias, 4)
        m["event_tier"] = (
            "high" if event_score >= 6.5 else "medium" if event_score >= 3.0 else "low"
        )
    return metrics


def _default_news_metric() -> dict:
    return {
        "news_count": 0,
        "high_importance_count": 0,
        "importance_weighted": 0.0,
        "event_bias_sum": 0.0,
        "latest_age_hours": None,
        "event_score": 0.0,
        "event_bias": 0.0,
        "event_tier": "low",
    }


def _normalize_news_metric(value: dict | None) -> dict:
    base = _default_news_metric()
    if isinstance(value, dict):
        for key in base.keys():
            if key in value and value.get(key) is not None:
                base[key] = value.get(key)
    return base


def _build_cross_section_features(candidates: list[EntryCandidate]) -> dict[int, dict]:
    by_market: dict[str, list[EntryCandidate]] = {}
    for c in candidates:
        market = (c.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(market, []).append(c)

    out: dict[int, dict] = {}
    for market, rows in by_market.items():
        n = len(rows)
        if n <= 0:
            continue
        score_values = {
            int(c.id): _finite_float(c.score)
            for c in rows if c.id is not None
        }
        change_values = {
            int(c.id): _finite_float(_extract_candidate_quote_change_pct(c))
            for c in rows
            if c.id is not None
        }
        turnover_values = {
            int(c.id): _finite_float(_extract_candidate_turnover(c))
            for c in rows
            if c.id is not None
        }
        vol_values = {
            int(c.id): _finite_float(_extract_candidate_volume_ratio(c))
            for c in rows
            if c.id is not None
        }
        score_pct = rank_map(score_values, reverse=True)
        change_pct = rank_map(change_values, reverse=True)
        turnover_pct = rank_map(turnover_values, reverse=True)
        vol_pct = rank_map(vol_values, reverse=True)

        for c in rows:
            if c.id is None:
                continue
            cid = int(c.id)
            score_rank = score_pct.get(cid)
            change_rank = change_pct.get(cid)
            turnover_rank = turnover_pct.get(cid)
            volume_rank = vol_pct.get(cid)
            # The v2 scoring config explicitly imputes missing percentile features to
            # 50 for the composite. Their captured source/rank values remain NULL.
            rs = 0.45 * (50.0 if score_rank is None else score_rank)
            rs += 0.25 * (50.0 if change_rank is None else change_rank)
            rs += 0.20 * (50.0 if turnover_rank is None else turnover_rank)
            rs += 0.10 * (50.0 if volume_rank is None else volume_rank)
            crowd = 0.0
            if rs >= 92:
                crowd += 2.5
            elif rs >= 85:
                crowd += 1.5
            if ((turnover_rank if turnover_rank is not None else 50.0) >= 95.0
                    and (change_rank if change_rank is not None else 50.0) >= 92.0):
                crowd += 1.5

            out[cid] = {
                "market": market,
                "score_pct": score_rank,
                "change_pct_rank": change_rank,
                "turnover_pct_rank": turnover_rank,
                "volume_pct_rank": volume_rank,
                "relative_strength_pct": _clamp(rs, 0.0, 100.0),
                "crowding_risk": _clamp(crowd, 0.0, 6.0),
                "feature_values": {
                    "candidate_score": _finite_float(c.score),
                    "change_pct": change_values.get(cid),
                    "turnover": turnover_values.get(cid),
                    "volume_ratio": vol_values.get(cid),
                },
                "missing_feature_policy": MISSING_FEATURE_POLICY,
            }
    return out


CONSTRAINT_LABELS = {
    "UNHELD_CAP": "未持倉儀器數超過上限",
    "HIGH_RISK_RATIO": "高風險儀器比例超過上限",
    "STRATEGY_SHARE": "單一策略儀器比例超過上限",
    "DUPLICATE_INSTRUMENT": "同一儀器的其他策略列不占預算",
    "INSTRUMENT_GROUP_UNKNOWN": "分類權威未知，未納入台股機會",
    "INSTRUMENT_ID_MISSING": "缺少可驗證的台股儀器身分",
    "SMALL_SET_QUOTA_EXCEPTION": "小樣本整數 quota 例外",
    "INVALID_RANKING_INPUT": "排名輸入不是有限數值",
    "DECISION_REFERENCE_UNAVAILABLE": "缺少不可變排名決策參考，已停止納入組合",
}


def _add_constraint_reason(row: StrategySignalRun, code: str, *, demote: bool = False) -> None:
    reasons = list(getattr(row, "constraint_reasons", None) or [])
    if code not in reasons:
        reasons.append(code)
    row.constraint_reasons = reasons
    row.constrained = bool(reasons)
    payload = row.payload if isinstance(row.payload, dict) else {}
    payload["constraint_reasons"] = reasons
    payload["constrained"] = row.constrained
    row.payload = to_jsonable(payload)
    if demote:
        row.status = "inactive"
        row.action = "watch"
        row.action_label = "觀望"
        label = CONSTRAINT_LABELS.get(code, code)
        row.reason = f"{row.reason}；{label}" if row.reason else label
    row.updated_at = utc_now()


def _demote_signal(row: StrategySignalRun, *, code: str) -> None:
    # Presentation demotions must not alter the ranker-v2 display tier or its raw
    # ordering value. The old rank score remains stored as-is for legacy rows too.
    _add_constraint_reason(row, code, demote=True)


def _is_eligible_unheld(row: StrategySignalRun) -> bool:
    if (row.status or "inactive").lower() != "active" or bool(row.is_holding_snapshot):
        return False
    if (row.action or "watch").lower() not in ("buy", "add"):
        return False
    return True


def _has_valid_ranking_value(row: StrategySignalRun) -> bool:
    if str(getattr(row, "ranker_version", "") or "") == RANKER_VERSION_V2:
        value = _safe_float(getattr(row, "ranking_value", None))
        return value is not None and isfinite(value)
    return True


def _risk_rank(value: str | None) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get((value or "medium").strip().lower(), 1)


def _quota(n: int, share: float, *, rounding: str = "floor") -> int:
    if n <= 0:
        return 0
    raw = n * share
    count = int(round(raw)) if rounding == "round" else int(raw // 1)
    return max(1, count)


def _tw_equity_selection(representatives: list[tuple[StrategySignalRun, list[StrategySignalRun]]]) -> dict:
    return _tw_instrument_selection(representatives, cap=TW_INSTRUMENT_CAP,
                                    risk_share=TW_HIGH_RISK_SHARE,
                                    strategy_share=TW_SINGLE_STRATEGY_SHARE)


def _tw_instrument_selection(representatives: list[tuple[StrategySignalRun, list[StrategySignalRun]]],
                             *, cap: int, risk_share: float,
                             strategy_share: float) -> dict:
    ordered = sorted(representatives, key=lambda pair: representative_sort_key(pair[0]))
    selected = list(ordered[:cap])
    excluded: dict[str, list[str]] = {
        canonical_instrument_id(rep): ["UNHELD_CAP"]
        for rep, _ in ordered[cap:]
    }
    cap_selected_count = len(selected)
    while selected:
        n = len(selected)
        risk_quota = _quota(n, risk_share)
        high = [pair for pair in selected if pair[1] and max(_risk_rank(x.risk_level) for x in pair[1]) >= 2]
        strategy_quotas = {
            str(rep.strategy_code or "unknown"): _quota(n, strategy_share)
            for rep, _ in selected
        }
        strategy_counts: dict[str, int] = {}
        for rep, _ in selected:
            code = str(rep.strategy_code or "unknown")
            strategy_counts[code] = strategy_counts.get(code, 0) + 1
        # Consider every simultaneously violating quota. Remove the weakest member of
        # the union, retain all applicable reasons, then recompute against the new n.
        violations: dict[str, set[str]] = {}
        if len(high) > risk_quota:
            high.sort(key=lambda pair: representative_sort_key(pair[0]))
            for rep, _ in high[-(len(high) - risk_quota):]:
                violations.setdefault(canonical_instrument_id(rep), set()).add("HIGH_RISK_RATIO")
        for code, count in strategy_counts.items():
            quota = strategy_quotas.get(code, 0)
            if count <= quota:
                continue
            members = sorted(
                [pair for pair in selected if str(pair[0].strategy_code or "unknown") == code],
                key=lambda pair: representative_sort_key(pair[0]),
            )
            for rep, _ in members[-(count - quota):]:
                violations.setdefault(canonical_instrument_id(rep), set()).add("STRATEGY_SHARE")
        candidates_to_prune = [
            (rep, members, violations.get(canonical_instrument_id(rep), set()))
            for rep, members in selected
            if canonical_instrument_id(rep) in violations
        ]
        if not candidates_to_prune:
            break
        weakest = max(candidates_to_prune, key=lambda item: representative_sort_key(item[0]))
        selected.remove((weakest[0], weakest[1]))
        excluded[canonical_instrument_id(weakest[0])] = sorted(weakest[2])

    final_n = len(selected)
    final_risk_quota = _quota(final_n, risk_share)
    final_strategy_quota = _quota(final_n, strategy_share)
    high_count = sum(
        1 for _, members in selected
        if members and max(_risk_rank(row.risk_level) for row in members) >= 2
    )
    final_strategy_counts: dict[str, int] = {}
    for rep, _ in selected:
        code = str(rep.strategy_code or "unknown")
        final_strategy_counts[code] = final_strategy_counts.get(code, 0) + 1
    exceptions: list[dict] = []
    if final_n and high_count / final_n > risk_share:
        exceptions.append({"type": "high_risk", "actual_ratio": high_count / final_n,
                           "share": risk_share, "quota": final_risk_quota})
    for code, count in sorted(final_strategy_counts.items()):
        if final_n and count / final_n > strategy_share:
            exceptions.append({"type": "strategy_share", "strategy_code": code,
                               "actual_ratio": count / final_n,
                               "share": strategy_share,
                               "quota": final_strategy_quota})
    return {
        "selected": selected,
        "excluded": excluded,
        "before_instrument_count": len(ordered),
        "cap_selected_count": cap_selected_count,
        "after_instrument_count": final_n,
        "risk_quota": final_risk_quota,
        "strategy_quotas": {key: final_strategy_quota for key in sorted(final_strategy_counts)},
        "risk_actual_ratio": high_count / final_n if final_n else 0.0,
        "strategy_actual_ratios": {
            key: count / final_n for key, count in sorted(final_strategy_counts.items())
        } if final_n else {},
        "small_set_quota_exceptions": exceptions,
        "cap": cap,
        "high_risk_share": risk_share,
        "strategy_share": strategy_share,
    }


def _selection_run_id(*, market: str, instrument_group: str, snapshot_date: str,
                      capture_id: str, config_version: str, policy_version: str) -> str:
    identity = "|".join((market, instrument_group, snapshot_date, capture_id,
                         config_version, policy_version))
    return f"selection-{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _persist_selection_ledger(*, db, rows: list[StrategySignalRun], market: str,
                              instrument_group: str, snapshot_date: str, capture_ref: dict,
                              constraint_unit: str, cap: int | None,
                              high_risk_share: float | None, strategy_share: float | None,
                              before_row_count: int, after_row_count: int,
                              before_instrument_count: int, after_instrument_count: int,
                              quota_metadata: dict, representatives: list[tuple],
                              policy_version: str) -> str:
    if db is None:
        return ""
    capture_id = str(capture_ref.get("capture_id") or "")
    config_version = str(capture_ref.get("scoring_config_version") or "")
    reference_hash = str(capture_ref.get("ranking_reference_hash") or "")
    ranker_version = str(capture_ref.get("ranker_version") or RANKER_VERSION_V2)
    snapshot_id = capture_ref.get("snapshot_id")
    decision_map = capture_ref.get("decision_ids_by_signal") or {}
    if not capture_id or not config_version or not reference_hash or not snapshot_id:
        return ""
    linked_rows: list[StrategySignalRun] = []
    unlinked_audit_rows: list[dict] = []
    for row in rows:
        row_id = getattr(row, "id", None)
        if (row_id is None or not row.decision_snapshot_id
                or decision_map.get(int(row_id)) != row.decision_snapshot_id):
            if bool(getattr(row, "selected_for_portfolio", False)):
                return ""
            unlinked_audit_rows.append({
                "signal_run_id": int(row_id) if row_id is not None else None,
                "instrument_id": canonical_instrument_id(row),
                "strategy_code": str(row.strategy_code or ""),
                "reason_codes": list(getattr(row, "constraint_reasons", None) or []),
                "audit_status": "missing_immutable_decision_reference",
            })
            continue
        linked_rows.append(row)
    if not linked_rows:
        return ""
    archived = db.query(RankingSnapshot).filter_by(id=int(snapshot_id)).first()
    if (not archived or archived.capture_id != capture_id
            or archived.scoring_config_version != config_version
            or str(getattr(archived, "ranking_reference_hash", "") or "") != reference_hash
            or not (getattr(archived, "ranking_reference", None) or {}).get("items")):
        return ""
    archived_items = {
        int(item.signal_run_id): str(item.decision_snapshot_id)
        for item in db.query(RankingSnapshotItem)
        .filter(RankingSnapshotItem.ranking_snapshot_id == int(snapshot_id))
        .all()
        if item.signal_run_id is not None
    }
    if any(archived_items.get(int(row.id)) != row.decision_snapshot_id for row in linked_rows):
        return ""
    if any(archived_items.get(int(row.id)) != row.decision_snapshot_id
           for row in linked_rows if bool(getattr(row, "selected_for_portfolio", False))):
        return ""
    run_id = _selection_run_id(
        market=market, instrument_group=instrument_group, snapshot_date=snapshot_date,
        capture_id=capture_id, config_version=config_version, policy_version=policy_version,
    )
    existing = db.query(PortfolioSelectionRun).filter_by(selection_run_id=run_id).first()
    if existing:
        return run_id
    selected_ids = {int(rep.id) for rep, _ in representatives if getattr(rep, "id", None) is not None
                    and bool(getattr(rep, "selected_for_portfolio", False))}
    decision_ids = sorted({str(row.decision_snapshot_id) for row in linked_rows
                           if getattr(row, "decision_snapshot_id", None)})
    if unlinked_audit_rows:
        quota_metadata = {
            **(quota_metadata if isinstance(quota_metadata, dict) else {}),
            "unlinked_audit_rows": unlinked_audit_rows,
        }
    run = PortfolioSelectionRun(
        selection_run_id=run_id,
        capture_id=capture_id,
        stock_market=market,
        instrument_group=instrument_group,
        snapshot_date=snapshot_date,
        constraint_unit=constraint_unit,
        ranker_version=ranker_version,
        scoring_config_version=config_version,
        policy_version=policy_version,
        cap=cap,
        high_risk_share=high_risk_share,
        strategy_share=strategy_share,
        before_row_count=before_row_count,
        after_row_count=after_row_count,
        before_instrument_count=before_instrument_count,
        after_instrument_count=after_instrument_count,
        quota_metadata=to_jsonable(quota_metadata),
        decision_snapshot_ids=decision_ids,
    )
    db.add(run)
    db.flush()
    members_by_signal: dict[int, tuple[StrategySignalRun, bool]] = {}
    for rep, members in representatives:
        for row in members:
            if getattr(row, "id", None) is not None:
                members_by_signal[int(row.id)] = (rep, row is rep)
    for row in linked_rows:
        rep, is_representative = members_by_signal.get(int(row.id), (row, False))
        reasons = list(getattr(row, "constraint_reasons", None) or [])
        db.add(PortfolioConstraintLedger(
            selection_run_id=run_id,
            stock_market=market,
            snapshot_date=snapshot_date,
            instrument_id=canonical_instrument_id(row),
            strategy_code=str(row.strategy_code or ""),
            signal_run_id=int(row.id),
            decision_snapshot_id=getattr(row, "decision_snapshot_id", None),
            is_representative=is_representative,
            selected_for_portfolio=bool(getattr(row, "selected_for_portfolio", False))
            and int(row.id) in selected_ids,
            constrained=bool(getattr(row, "constrained", False)),
            reason_codes=reasons,
            constraint_unit=constraint_unit,
            before_row_count=before_row_count,
            after_row_count=after_row_count,
            before_instrument_count=before_instrument_count,
            after_instrument_count=after_instrument_count,
            quota_metadata=to_jsonable({**quota_metadata,
                "strategy_risk_level": row.risk_level,
                "group_risk_level": (row.selection_metadata or {}).get("group_risk_level", row.risk_level),
            }),
            ranker_version=ranker_version,
            scoring_config_version=config_version,
            policy_version=policy_version,
        ))
    return run_id


def _has_row_capture(row: StrategySignalRun, capture_ref: dict | None) -> bool:
    ref = capture_ref if isinstance(capture_ref, dict) else {}
    decision_map = ref.get("decision_ids_by_signal") or {}
    return bool(
        getattr(row, "id", None) is not None
        and getattr(row, "decision_snapshot_id", None)
        and decision_map.get(int(row.id)) == row.decision_snapshot_id
    )


def _has_complete_capture(rows: list[StrategySignalRun], capture_ref: dict | None) -> bool:
    """Require each budget-eligible row to link to a sealed decision."""
    ref = capture_ref if isinstance(capture_ref, dict) else {}
    capture_id = str(ref.get("capture_id") or "")
    config_version = str(ref.get("scoring_config_version") or "")
    reference_hash = str(ref.get("ranking_reference_hash") or "")
    ranker_version = str(ref.get("ranker_version") or "")
    snapshot_id = ref.get("snapshot_id")
    decision_map = ref.get("decision_ids_by_signal") or {}
    if not capture_id or not config_version or not reference_hash or not ranker_version or not snapshot_id:
        return False
    return all(_has_row_capture(row, ref) for row in rows)


def _tw_etf_row_selection(rows: list[StrategySignalRun], record_reason) -> dict:
    ordered = sorted(rows, key=representative_sort_key)
    reasons: dict[int, list[str]] = {id(row): ["UNHELD_CAP"] for row in ordered[20:]}
    retained = ordered[:20]
    high = [row for row in retained if _risk_rank(row.risk_level) >= 2]
    risk_quota = _quota(len(retained), TW_HIGH_RISK_SHARE, rounding="round")
    for row in high[risk_quota:]:
        reasons[id(row)] = ["HIGH_RISK_RATIO"]
    retained = [row for row in retained if id(row) not in reasons]
    strategy_quota = _quota(len(retained), TW_SINGLE_STRATEGY_SHARE, rounding="round")
    for code in sorted({row.strategy_code for row in retained}):
        for row in [row for row in retained if row.strategy_code == code][strategy_quota:]:
            reasons[id(row)] = ["STRATEGY_SHARE"]
    for row in ordered:
        for code in reasons.get(id(row), []):
            record_reason(row, code, demote=True)
    final = [row for row in retained if id(row) not in reasons]
    selected = group_instrument_rows(final)
    selected_ids = {canonical_instrument_id(rep) for rep, _ in selected}
    groups = group_instrument_rows(ordered)
    excluded = {canonical_instrument_id(rep): sorted({code for row in members for code in reasons.get(id(row), [])})
                for rep, members in groups if canonical_instrument_id(rep) not in selected_ids}
    n = len(final)
    counts = {code: sum(row.strategy_code == code for row in final) for code in sorted({row.strategy_code for row in final})}
    return {"selected": selected, "excluded": excluded,
            "before_instrument_count": len(groups), "cap_selected_count": min(20, len(ordered)),
            "after_instrument_count": len(selected), "risk_quota": risk_quota,
            "strategy_quotas": {code: strategy_quota for code in counts},
            "risk_actual_ratio": sum(_risk_rank(row.risk_level) >= 2 for row in final) / n if n else 0,
            "strategy_actual_ratios": {code: count / n for code, count in counts.items()} if n else {},
            "small_set_quota_exceptions": []}


def _apply_portfolio_constraints(*, rows: list[StrategySignalRun], db=None,
                                 capture_refs: dict | None = None,
                                 snapshot_date: str = "") -> dict:
    """Apply stable selection and persist a versioned instrument/row ledger."""
    by_market: dict[str, list[StrategySignalRun]] = {}
    for row in rows:
        row.selected_for_portfolio = False
        row.constrained = False
        row.constraint_reasons = []
        row.selection_metadata = {}
        payload = dict(row.payload or {})
        payload.update(constrained=False, constraint_reasons=[])
        row.payload = payload
        market = (row.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(market, []).append(row)

    demoted = 0
    by_reason: dict[str, int] = {}
    run_ids: list[str] = []
    run_metadata: list[dict] = []
    capture_refs = capture_refs or {}

    def record_reason(row: StrategySignalRun, code: str, *, demote: bool = False) -> None:
        nonlocal demoted
        had_reason = code in list(getattr(row, "constraint_reasons", None) or [])
        if demote and (row.status or "inactive").lower() == "active":
            demoted += 1
        _add_constraint_reason(row, code, demote=demote)
        if not had_reason:
            by_reason[code] = by_reason.get(code, 0) + 1

    for market, market_rows in sorted(by_market.items()):
        capture_ref = capture_refs.get(market) or {}
        eligible_before = [row for row in market_rows if _is_eligible_unheld(row)]
        capture_required_rows = eligible_before
        if market == "TW":
            capture_required_rows = [
                row for row in eligible_before
                if str(row.instrument_group or "").strip().upper() in ("EQUITY", "ETF")
                and canonical_instrument_id(row).startswith(("TWSE:", "TPEX:"))
            ]
        # Runtime writes fail closed without immutable evidence for each row that
        # could consume a budget slot. Unclassified/unresolved TW rows are audited
        # individually and cannot make unrelated canonical instruments ineligible.
        capture_complete = (db is None) or _has_complete_capture(capture_required_rows, capture_ref)
        capture_required_ids = {id(row) for row in capture_required_rows}
        eligible = list(eligible_before)
        for row in eligible:
            if not _has_valid_ranking_value(row):
                record_reason(row, "INVALID_RANKING_INPUT", demote=True)
            if market == "TW":
                group = str(row.instrument_group or "UNKNOWN").strip().upper()
                instrument = canonical_instrument_id(row)
                venue_ok = instrument.startswith(("TWSE:", "TPEX:"))
                if group not in ("EQUITY", "ETF"):
                    record_reason(row, "INSTRUMENT_GROUP_UNKNOWN", demote=True)
                elif not venue_ok:
                    record_reason(row, "INSTRUMENT_ID_MISSING", demote=True)
            row_has_capture = _has_row_capture(row, capture_ref)
            if db is not None and id(row) in capture_required_ids and not capture_complete:
                record_reason(row, "DECISION_REFERENCE_UNAVAILABLE", demote=True)
            elif db is not None and id(row) not in capture_required_ids and not row_has_capture:
                record_reason(row, "DECISION_REFERENCE_UNAVAILABLE", demote=True)
        eligible = [row for row in eligible if (row.status or "").lower() == "active"
                    and _has_valid_ranking_value(row)
                    and (capture_complete or id(row) not in capture_required_ids)]

        if market == "TW":
            eligible = [row for row in eligible if (row.status or "").lower() == "active"
                        and str(row.instrument_group or "").upper() in ("EQUITY", "ETF")
                        and canonical_instrument_id(row).startswith(("TWSE:", "TPEX:"))]

            audit_rows_by_kind: dict[str, list[StrategySignalRun]] = {
                "EQUITY": [], "ETF": [], "UNKNOWN": [],
            }
            for row in market_rows:
                kind = str(row.instrument_group or "UNKNOWN").strip().upper()
                audit_rows_by_kind[kind if kind in ("EQUITY", "ETF") else "UNKNOWN"].append(row)
            eligible_by_kind = {
                kind: [row for row in eligible if str(row.instrument_group or "").upper() == kind]
                for kind in ("EQUITY", "ETF")
            }
            for kind in ("EQUITY", "ETF"):
                audit_rows = audit_rows_by_kind[kind]
                cohort_rows = eligible_by_kind[kind]
                grouped = group_instrument_rows(cohort_rows)
                if kind == "EQUITY":
                    cap = TW_INSTRUMENT_CAP
                    policy = TW_CONSTRAINT_POLICY_VERSION
                else:
                    # ETFs retain their independent legacy row cap and rounding.
                    cap = 20
                    policy = "tw-etf-row-constraint-v1"
                selection = (_tw_instrument_selection(
                    grouped, cap=cap, risk_share=TW_HIGH_RISK_SHARE,
                    strategy_share=TW_SINGLE_STRATEGY_SHARE,
                ) if kind == "EQUITY" else _tw_etf_row_selection(cohort_rows, record_reason))
                selected_pairs = selection["selected"]
                selected_by_id = {canonical_instrument_id(rep): rep for rep, _ in selected_pairs}
                grouped = [(selected_by_id.get(canonical_instrument_id(rep), rep), members)
                           for rep, members in grouped]
                excluded = selection["excluded"]
                selected_ids = {canonical_instrument_id(rep) for rep, _ in selected_pairs}
                audit_groups = group_instrument_rows(audit_rows)
                for rep, members in grouped:
                    instrument_id = canonical_instrument_id(rep)
                    if instrument_id in selected_ids:
                        rep.selected_for_portfolio = True
                    else:
                        reasons = excluded.get(instrument_id, ["UNHELD_CAP"])
                        for row in members:
                            for code in reasons:
                                record_reason(row, code, demote=True)
                    for row in members:
                        if row is not rep:
                            record_reason(row, "DUPLICATE_INSTRUMENT")

                exception_ids: set[str] = set()
                for exception in selection["small_set_quota_exceptions"]:
                    if exception["type"] == "high_risk":
                        exception_ids.update(
                            canonical_instrument_id(rep) for rep, members in selected_pairs
                            if members and max(_risk_rank(x.risk_level) for x in members) >= 2
                        )
                    else:
                        exception_ids.update(
                            canonical_instrument_id(rep) for rep, _ in selected_pairs
                            if str(rep.strategy_code or "unknown") == exception.get("strategy_code")
                        )
                for rep, _ in selected_pairs:
                    if canonical_instrument_id(rep) in exception_ids:
                        record_reason(rep, "SMALL_SET_QUOTA_EXCEPTION")

                retained_rows = sum(1 for row in cohort_rows
                                    if (row.status or "").lower() == "active")
                budget_count = sum(1 for rep, _ in selected_pairs
                                   if bool(rep.selected_for_portfolio))
                quota_meta = {
                    "cap_binding": any("UNHELD_CAP" in reasons for reasons in excluded.values()),
                    "cap_excluded_instrument_count": sum("UNHELD_CAP" in reasons for reasons in excluded.values()),
                    "initial_cap_selected_count": selection["cap_selected_count"],
                    "final_instrument_count": selection["after_instrument_count"],
                    "before_instrument_count": selection["before_instrument_count"],
                    "after_instrument_count": budget_count,
                    "before_row_count": len(cohort_rows),
                    "after_row_count": retained_rows,
                    "high_risk_quota": selection["risk_quota"],
                    "high_risk_actual_ratio": selection["risk_actual_ratio"],
                    "strategy_quotas": selection["strategy_quotas"],
                    "strategy_actual_ratios": selection["strategy_actual_ratios"],
                    "small_set_quota_exceptions": selection["small_set_quota_exceptions"],
                    "eligible_row_count": len(cohort_rows),
                    "retained_active_strategy_rows": retained_rows,
                    "held_management_row_count": sum(
                        1 for row in audit_rows if bool(row.is_holding_snapshot)
                    ),
                    "selected_representative_count": budget_count,
                    "budget_input_count": budget_count,
                    "deduplicate_by_instrument": True,
                    "no_replenishment": True,
                    "cap": cap,
                    "constraint_unit": "INSTRUMENT" if kind == "EQUITY" else "ROW",
                    "policy_version": policy,
                }
                for row in audit_rows:
                    members = next((members for rep, members in grouped
                                    if canonical_instrument_id(rep) == canonical_instrument_id(row)),
                                   next((members for rep, members in audit_groups
                                         if canonical_instrument_id(rep) == canonical_instrument_id(row)), [row]))
                    group_risk = max(members, key=lambda member: _risk_rank(member.risk_level)).risk_level
                    row.selection_metadata = {**quota_meta, "instrument_group": kind,
                                              "group_risk_level": group_risk}
                run_id = _persist_selection_ledger(
                    db=db, rows=audit_rows, market=market, instrument_group=kind,
                    snapshot_date=snapshot_date, capture_ref=capture_ref,
                    constraint_unit=quota_meta["constraint_unit"], cap=cap,
                    high_risk_share=TW_HIGH_RISK_SHARE,
                    strategy_share=TW_SINGLE_STRATEGY_SHARE,
                    before_row_count=len(cohort_rows), after_row_count=retained_rows,
                    before_instrument_count=len(grouped), after_instrument_count=budget_count,
                    quota_metadata=quota_meta, representatives=grouped,
                    policy_version=policy,
                )
                if db is not None and not run_id and budget_count:
                    quota_meta["planned_final_instrument_count"] = budget_count
                    quota_meta["selection_evidence_available"] = False
                    for rep, members in selected_pairs:
                        for row in members:
                            row.selected_for_portfolio = False
                            record_reason(row, "DECISION_REFERENCE_UNAVAILABLE", demote=True)
                    budget_count = 0
                    retained_rows = sum(1 for row in cohort_rows
                                        if (row.status or "").lower() == "active")
                    quota_meta["final_instrument_count"] = 0
                    quota_meta["after_instrument_count"] = 0
                    quota_meta["after_row_count"] = retained_rows
                    quota_meta["retained_active_strategy_rows"] = retained_rows
                    quota_meta["selected_representative_count"] = 0
                    quota_meta["budget_input_count"] = 0
                    for row in audit_rows:
                        row.selection_metadata = {**quota_meta, "instrument_group": kind}
                if run_id:
                    run_ids.append(run_id)
                run_metadata.append({"market": market, "instrument_group": kind,
                                     "before_rows": len(cohort_rows),
                                     "after_rows": retained_rows,
                                     "before_instruments": len(grouped),
                                     "after_instruments": budget_count, **quota_meta})

            unknown_rows = audit_rows_by_kind["UNKNOWN"]
            for row in unknown_rows:
                if _is_eligible_unheld(row) and row.status == "active":
                    record_reason(row, "INSTRUMENT_GROUP_UNKNOWN", demote=True)
                row.selection_metadata = {"instrument_group": "UNKNOWN",
                                          "constraint_unit": "NONE",
                                          "policy_version": "tw-unknown-excluded-v1"}
            if unknown_rows:
                _persist_selection_ledger(
                    db=db, rows=unknown_rows, market=market, instrument_group="UNKNOWN",
                    snapshot_date=snapshot_date, capture_ref=capture_ref,
                    constraint_unit="NONE", cap=0, high_risk_share=None,
                    strategy_share=None, before_row_count=0, after_row_count=0,
                    before_instrument_count=0, after_instrument_count=0,
                    quota_metadata={"eligible_row_count": 0, "budget_input_count": 0,
                                    "excluded_group": True},
                    representatives=group_instrument_rows(unknown_rows),
                    policy_version="tw-unknown-excluded-v1",
                )
            continue

        # CN/HK/US retain their existing row-count caps and Python-round rules.
        cohort_rows = sorted(eligible, key=representative_sort_key)
        max_unheld = int(MAX_UNHELD_ACTIVE_BY_MARKET.get(market, 20))
        remaining_rows = cohort_rows[:max_unheld]
        for row in cohort_rows[max_unheld:]:
            record_reason(row, "UNHELD_CAP", demote=True)
        risk_share = float(MAX_HIGH_RISK_RATIO_BY_MARKET.get(market, 0.32))
        high_rows = sorted([row for row in remaining_rows if _risk_rank(row.risk_level) >= 2],
                           key=representative_sort_key)
        allowed_high = _quota(len(remaining_rows), risk_share, rounding="round")
        for row in high_rows[allowed_high:]:
            record_reason(row, "HIGH_RISK_RATIO", demote=True)
        remaining_rows = [row for row in remaining_rows if row.status == "active"]
        strategy_share = float(MAX_SINGLE_STRATEGY_SHARE_BY_MARKET.get(
            market, MAX_SINGLE_STRATEGY_SHARE
        ))
        strategy_cap = _quota(len(remaining_rows), strategy_share, rounding="round")
        by_strategy: dict[str, list[StrategySignalRun]] = {}
        for row in remaining_rows:
            by_strategy.setdefault(str(row.strategy_code or "unknown"), []).append(row)
        for strategy_rows in by_strategy.values():
            strategy_rows.sort(key=representative_sort_key)
            for row in strategy_rows[strategy_cap:]:
                record_reason(row, "STRATEGY_SHARE", demote=True)
        final_rows = [row for row in remaining_rows if row.status == "active"]
        groups = group_instrument_rows(final_rows)
        for rep, members in groups:
            rep.selected_for_portfolio = True
            for row in members:
                if row is not rep:
                    record_reason(row, "DUPLICATE_INSTRUMENT")
        for row in market_rows:
            row.selection_metadata = {"constraint_unit": "ROW", "cap": max_unheld,
                                      "high_risk_share": risk_share,
                                      "strategy_share": strategy_share,
                                      "policy_version": "legacy-row-constraint-v1"}
        budget_count = sum(1 for rep, _ in groups if rep.selected_for_portfolio)
        quota_meta = {
            "constraint_unit": "ROW", "cap": max_unheld,
            "high_risk_share": risk_share, "high_risk_quota": allowed_high,
            "strategy_share": strategy_share, "strategy_quota": strategy_cap,
            "rounding": "python-round-compatible",
            "retained_active_strategy_rows": len(final_rows),
            "selected_representative_count": budget_count,
            "budget_input_count": budget_count,
        }
        run_id = _persist_selection_ledger(
            db=db, rows=market_rows, market=market, instrument_group="ALL",
            snapshot_date=snapshot_date, capture_ref=capture_ref,
            constraint_unit="ROW", cap=max_unheld, high_risk_share=risk_share,
            strategy_share=strategy_share, before_row_count=len(eligible),
            after_row_count=len(final_rows),
            before_instrument_count=len(group_instrument_rows(eligible)),
            after_instrument_count=budget_count, quota_metadata=quota_meta,
            representatives=groups,
            policy_version="legacy-row-constraint-v1",
        )
        if db is not None and not run_id and budget_count:
            quota_meta["planned_selected_representative_count"] = budget_count
            quota_meta["selection_evidence_available"] = False
            for rep, members in groups:
                for row in members:
                    row.selected_for_portfolio = False
                    record_reason(row, "DECISION_REFERENCE_UNAVAILABLE", demote=True)
            budget_count = 0
            final_rows = [row for row in remaining_rows if row.status == "active"]
            quota_meta["retained_active_strategy_rows"] = len(final_rows)
            quota_meta["selected_representative_count"] = 0
            quota_meta["budget_input_count"] = 0
            for row in market_rows:
                row.selection_metadata = {**quota_meta}
        if run_id:
            run_ids.append(run_id)
        run_metadata.append({"market": market, "instrument_group": "ALL",
                             **quota_meta, "before_rows": len(eligible),
                             "after_rows": len(final_rows),
                             "before_instruments": len(group_instrument_rows(eligible)),
                             "after_instruments": budget_count})

    return {"demoted": demoted, "by_reason": by_reason,
            "selection_run_ids": run_ids, "selection_runs": run_metadata}


def _compute_factor_breakdown(
    *,
    row: EntryCandidate,
    strategy_code: str,
    weight: float,
    risk_level: str,
    regime_info: dict | None,
    cross_feature: dict | None = None,
    news_metric: dict | None = None,
    factor_weights: dict | None = None,
) -> dict:
    base_score_input = _finite_float(row.score)
    base_score = float(base_score_input or 0.0)
    action = (row.action or "watch").strip().lower() or "watch"
    is_holding = bool(row.is_holding_snapshot)
    signal_text = f"{row.signal or ''} {row.reason or ''}".lower()
    plan_quality = int(row.plan_quality or 0)
    quote_change_pct = _finite_float(_extract_candidate_quote_change_pct(row))
    volume_ratio = _finite_float(_extract_candidate_volume_ratio(row))
    turnover = _finite_float(_extract_candidate_turnover(row))
    is_market_scan = (row.candidate_source or "").strip() in ("market_scan", "mixed")
    cf = cross_feature if isinstance(cross_feature, dict) else {}
    nm = news_metric if isinstance(news_metric, dict) else {}
    relative_strength_pct = _finite_float(cf.get("relative_strength_pct"))
    crowding_risk = _finite_float(cf.get("crowding_risk")) or 0.0
    event_score = float(_finite_float(nm.get("event_score")) or 0.0)
    event_bias = float(_finite_float(nm.get("event_bias")) or 0.0)
    event_count = int(nm.get("news_count") or 0)

    alpha_score = _clamp((base_score - 50.0) * 0.45, -12.0, 18.0)
    if relative_strength_pct is not None:
        alpha_score += _clamp((relative_strength_pct - 50.0) / 15.0, -2.0, 4.0)
    catalyst_score = 0.0
    if is_market_scan:
        catalyst_score += 2.5
    if quote_change_pct is not None:
        if 1.0 <= quote_change_pct <= 7.0:
            catalyst_score += 4.0
        elif quote_change_pct > 9.0:
            catalyst_score += 1.5
        elif quote_change_pct < -4.0:
            catalyst_score -= 2.5
    if ("突破" in signal_text) or ("breakout" in signal_text):
        catalyst_score += 2.5
    if "回踩" in signal_text:
        catalyst_score += 1.5
    if "超跌" in signal_text:
        catalyst_score += 1.0
    catalyst_score += _clamp(event_score * 0.55, -3.0, 6.5)
    if event_bias > 0.8:
        catalyst_score += 1.2
    if relative_strength_pct is not None:
        catalyst_score += _clamp((relative_strength_pct - 60.0) / 12.0, -2.5, 4.5)

    quality_score = _clamp((plan_quality - 50.0) / 5.0, -8.0, 10.0)
    if event_count >= 3:
        quality_score += 0.8

    risk_penalty = 0.0
    if risk_level == "high":
        risk_penalty += 1.5
    if quote_change_pct is not None and abs(quote_change_pct) >= 8.0:
        risk_penalty += 2.0
    if (row.status or "inactive") != "active":
        risk_penalty += 2.5
    if plan_quality < 70:
        risk_penalty += 1.5
    if event_bias < -0.9:
        risk_penalty += 2.2

    crowd_penalty = 0.0
    if quote_change_pct is not None and quote_change_pct >= 9.0:
        crowd_penalty += 2.5
    if volume_ratio is not None and volume_ratio >= 3.0:
        crowd_penalty += 1.5
    if turnover is not None and turnover >= 8_000_000_000:
        crowd_penalty += 1.0
    crowd_penalty += _clamp(crowding_risk, 0.0, 6.0)

    source_bonus = 0.0
    if (row.source_agent or "") in ("premarket_outlook", "intraday_monitor"):
        source_bonus += 1.0
    if strategy_code in ("trend_follow", "volume_breakout", "macd_golden"):
        source_bonus += 0.8
    if relative_strength_pct is not None and relative_strength_pct >= 80:
        source_bonus += 0.8

    regime = (regime_info or {}).get("regime") or "neutral"
    regime_confidence = float((regime_info or {}).get("confidence") or 0.0)
    regime_multiplier = 1.0
    if regime == "bullish":
        regime_multiplier = 1.06 if action in ("buy", "add") else 1.01
    elif regime == "bearish":
        regime_multiplier = 0.90 if action in ("buy", "add") else 0.97
    regime_multiplier += _clamp((regime_confidence - 0.5) * 0.06, -0.03, 0.03)
    regime_multiplier = _clamp(regime_multiplier, 0.85, 1.12)

    # 每因子外接權重(預設 1.0 → 行為 = 現狀,零迴歸)。snapshot 仍存 raw 因子分,
    # 權重只作用於合成,確保 IC 測在原始因子上(見 factor_calibration 設計要點)。
    fw = factor_weights or {}
    raw_score = (
        base_score
        + fw.get("alpha_score", 1.0) * alpha_score
        + fw.get("catalyst_score", 1.0) * catalyst_score
        + fw.get("quality_score", 1.0) * quality_score
        + source_bonus  # v1: source_bonus 權重固定 1.0
    )
    raw_score -= fw.get("risk_penalty", 1.0) * risk_penalty
    raw_score -= fw.get("crowd_penalty", 1.0) * crowd_penalty
    has_entry = row.entry_low is not None or row.entry_high is not None
    if action in ("buy", "add") and not has_entry:
        # No entry window means this is not executable; force into watch semantics.
        raw_score -= 8.0
    if action in ("buy", "add") and plan_quality < 90:
        raw_score -= 6.0
    ranking_value = raw_score * float(weight if weight is not None else 1.0) * regime_multiplier
    valid_ranking_input = (
        base_score_input is not None
        and isfinite(base_score_input)
        and isfinite(raw_score)
        and isfinite(ranking_value)
    )
    if valid_ranking_input:
        rank_score = stable_sigmoid_score(ranking_value)
    else:
        ranking_value = None
        rank_score = None

    return {
        # Preserve full inputs for the immutable ranking capture.
        "base_score": base_score_input,
        "alpha_score": alpha_score,
        "catalyst_score": catalyst_score,
        "quality_score": quality_score,
        "risk_penalty": risk_penalty,
        "crowd_penalty": crowd_penalty,
        "source_bonus": source_bonus,
        "regime": regime,
        "regime_label": _regime_label(regime),
        "regime_multiplier": regime_multiplier,
        "raw_score": raw_score if valid_ranking_input else None,
        "ranking_value": ranking_value,
        "rank_score": rank_score,
        "weighted_score": rank_score,
        "ranker_version": RANKER_VERSION_V2,
        "weight": float(weight if weight is not None else 1.0),
        "relative_strength_pct": float(relative_strength_pct or 0.0)
        if relative_strength_pct is not None
        else None,
        "event_score": float(event_score),
        "event_bias": float(event_bias),
        "event_count": event_count,
        "crowding_risk": float(crowding_risk or 0.0),
        "has_entry_plan": bool(has_entry),
    }


def _sync_factor_and_risk_snapshots(
    *,
    db,
    snapshot: str,
    signals: list[StrategySignalRun],
) -> None:
    run_ids = [int(s.id) for s in signals if s.id is not None]
    if not run_ids:
        return

    existing_factors = (
        db.query(StrategyFactorSnapshot)
        .filter(StrategyFactorSnapshot.snapshot_date == snapshot)
        .all()
    )
    factor_map = {int(x.signal_run_id): x for x in existing_factors}
    touched_ids: set[int] = set()

    for s in signals:
        if s.id is None:
            continue
        sid = int(s.id)
        payload = s.payload if isinstance(s.payload, dict) else {}
        breakdown = payload.get("score_breakdown") if isinstance(payload.get("score_breakdown"), dict) else {}
        row = factor_map.get(sid)
        if not row:
            row = StrategyFactorSnapshot(
                signal_run_id=sid,
                snapshot_date=snapshot,
                stock_symbol=s.stock_symbol,
                stock_market=s.stock_market,
                strategy_code=s.strategy_code,
            )
            db.add(row)
            factor_map[sid] = row
        row.snapshot_date = snapshot
        row.stock_symbol = s.stock_symbol
        row.stock_market = s.stock_market
        row.strategy_code = s.strategy_code
        row.alpha_score = float(breakdown.get("alpha_score") or 0.0)
        row.catalyst_score = float(breakdown.get("catalyst_score") or 0.0)
        row.quality_score = float(breakdown.get("quality_score") or 0.0)
        row.risk_penalty = float(breakdown.get("risk_penalty") or 0.0)
        row.crowd_penalty = float(breakdown.get("crowd_penalty") or 0.0)
        row.source_bonus = float(breakdown.get("source_bonus") or 0.0)
        row.regime_multiplier = float(breakdown.get("regime_multiplier") or 1.0)
        row.final_score = float(breakdown.get("weighted_score") or s.rank_score or 0.0)
        row.factor_payload = to_jsonable(
            {
                "score_breakdown": breakdown,
                "source_pool": s.source_pool or "watchlist",
                "risk_level": s.risk_level or "medium",
                "cross_feature": payload.get("cross_feature")
                if isinstance(payload.get("cross_feature"), dict)
                else {},
                "news_metric": _normalize_news_metric(
                    payload.get("news_metric")
                    if isinstance(payload.get("news_metric"), dict)
                    else None
                ),
                "constrained": bool(payload.get("constrained")),
                "constraint_reasons": payload.get("constraint_reasons")
                if isinstance(payload.get("constraint_reasons"), list)
                else [],
            }
        )
        row.updated_at = utc_now()
        touched_ids.add(sid)

    stale_factor_ids = [
        int(x.id)
        for x in existing_factors
        if x.id is not None and int(x.signal_run_id or -1) not in touched_ids
    ]
    if stale_factor_ids:
        db.query(StrategyFactorSnapshot).filter(
            StrategyFactorSnapshot.id.in_(stale_factor_ids)
        ).delete(synchronize_session=False)

    # Risk snapshot by snapshot_date + market
    by_market: dict[str, list[StrategySignalRun]] = {}
    for s in signals:
        market = (s.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(market, []).append(s)

    for market, rows in by_market.items():
        total = len(rows)
        active = sum(1 for x in rows if (x.status or "inactive") == "active")
        held = sum(1 for x in rows if bool(x.is_holding_snapshot))
        unheld = max(0, total - held)
        high_risk = sum(1 for x in rows if (x.risk_level or "medium") == "high")
        high_risk_ratio = (high_risk / total) if total else 0.0
        sorted_scores = sorted([float(x.rank_score or 0.0) for x in rows], reverse=True)
        score_sum = sum(sorted_scores)
        top5 = sum(sorted_scores[:5])
        concentration_top5 = (top5 / score_sum) if score_sum > 0 else 0.0
        avg_rank_score = (score_sum / total) if total else 0.0

        if high_risk_ratio >= 0.45 or concentration_top5 >= 0.65:
            risk_level = "high"
        elif high_risk_ratio >= 0.28 or concentration_top5 >= 0.48:
            risk_level = "medium"
        else:
            risk_level = "low"

        row = (
            db.query(PortfolioRiskSnapshot)
            .filter(
                PortfolioRiskSnapshot.snapshot_date == snapshot,
                PortfolioRiskSnapshot.market == market,
            )
            .first()
        )
        if not row:
            row = PortfolioRiskSnapshot(
                snapshot_date=snapshot,
                market=market,
            )
            db.add(row)
        row.total_signals = total
        row.active_signals = active
        row.held_signals = held
        row.unheld_signals = unheld
        row.high_risk_ratio = round(high_risk_ratio, 4)
        row.concentration_top5 = round(concentration_top5, 4)
        row.avg_rank_score = round(avg_rank_score, 4)
        row.risk_level = risk_level
        row.meta = to_jsonable(
            {
                "score_sum": round(score_sum, 4),
                "top5_score_sum": round(top5, 4),
            }
        )
        row.updated_at = utc_now()


def _format_signal(
    row: StrategySignalRun,
    *,
    include_payload: bool = True,
    factor_snapshot: StrategyFactorSnapshot | None = None,
) -> dict:
    payload_raw = row.payload if isinstance(row.payload, dict) else {}
    payload = _compact_signal_payload(payload_raw) if include_payload else {}

    if include_payload:
        score_breakdown = (
            payload.get("score_breakdown")
            if isinstance(payload.get("score_breakdown"), dict)
            else {}
        )
        market_regime = (
            payload.get("market_regime")
            if isinstance(payload.get("market_regime"), dict)
            else {}
        )
        cross_feature = (
            payload.get("cross_feature")
            if isinstance(payload.get("cross_feature"), dict)
            else {}
        )
        news_metric = _normalize_news_metric(
            payload.get("news_metric") if isinstance(payload.get("news_metric"), dict) else None
        )
        constraint_reasons = payload.get("constraint_reasons")
        if not isinstance(constraint_reasons, list):
            constraint_reasons = []
        constrained = bool(payload.get("constrained"))
    else:
        fp = (
            factor_snapshot.factor_payload
            if factor_snapshot is not None and isinstance(factor_snapshot.factor_payload, dict)
            else {}
        )
        market_regime = fp.get("market_regime") if isinstance(fp.get("market_regime"), dict) else {}
        cross_feature = fp.get("cross_feature") if isinstance(fp.get("cross_feature"), dict) else {}
        news_metric = _normalize_news_metric(
            fp.get("news_metric") if isinstance(fp.get("news_metric"), dict) else None
        )
        raw_reasons = fp.get("constraint_reasons")
        constraint_reasons = raw_reasons if isinstance(raw_reasons, list) else []
        constrained = bool(fp.get("constrained"))
        score_breakdown = {
            "alpha_score": round(float(getattr(factor_snapshot, "alpha_score", 0.0) or 0.0), 4),
            "catalyst_score": round(float(getattr(factor_snapshot, "catalyst_score", 0.0) or 0.0), 4),
            "quality_score": round(float(getattr(factor_snapshot, "quality_score", 0.0) or 0.0), 4),
            "risk_penalty": round(float(getattr(factor_snapshot, "risk_penalty", 0.0) or 0.0), 4),
            "crowd_penalty": round(float(getattr(factor_snapshot, "crowd_penalty", 0.0) or 0.0), 4),
            "source_bonus": round(float(getattr(factor_snapshot, "source_bonus", 0.0) or 0.0), 4),
            "regime_multiplier": round(
                float(getattr(factor_snapshot, "regime_multiplier", 1.0) or 1.0), 4
            ),
            "weighted_score": round(
                float(getattr(factor_snapshot, "final_score", row.rank_score) or row.rank_score or 0.0),
                4,
            ),
            "has_entry_plan": bool(row.entry_low is not None or row.entry_high is not None),
        }
    has_entry_plan = bool(
        row.entry_low is not None
        or row.entry_high is not None
        or (
            include_payload
            and
            isinstance(payload.get("source_meta"), dict)
            and isinstance(payload["source_meta"].get("plan"), dict)
            and (
                _safe_float(payload["source_meta"]["plan"].get("entry_low")) is not None
                or _safe_float(payload["source_meta"]["plan"].get("entry_high")) is not None
            )
        )
    )
    score_version = str(getattr(row, "score_version", "") or SCORE_VERSION_LEGACY)
    action, action_label, normalized_rank_score = _normalize_action_view(
        action=row.action or "watch",
        action_label=row.action_label or "",
        is_holding=bool(row.is_holding_snapshot),
        rank_score=float(row.rank_score or 0.0),
        has_entry_plan=has_entry_plan,
        score_version=score_version,
    )
    rank_score = (
        None
        if score_version == SCORE_VERSION_V2 and getattr(row, "ranking_value", None) is None
        else normalized_rank_score
    )
    stored_reasons = getattr(row, "constraint_reasons", None)
    if not isinstance(stored_reasons, list):
        stored_reasons = constraint_reasons
    return {
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "stock_symbol": row.stock_symbol,
        "stock_market": row.stock_market,
        "instrument_id": row.instrument_id,
        "instrument_group": row.instrument_group or "UNKNOWN",
        "instrument_authority": row.instrument_authority or {},
        "scan_run_id": row.scan_run_id,
        "scan_run_ids": row.scan_run_ids or [],
        "scan_run_refs": row.scan_run_refs or [],
        "stock_name": row.stock_name,
        "strategy_code": row.strategy_code,
        "strategy_name": row.strategy_name,
        "strategy_version": row.strategy_version,
        "risk_level": row.risk_level,
        "risk_level_label": _risk_label(row.risk_level or "medium"),
        "source_pool": row.source_pool or "watchlist",
        "source_pool_label": _source_label(row.source_pool or "watchlist"),
        "score": round(float(row.score or 0), 2),
        "rank_score": round(float(rank_score), 2) if rank_score is not None else None,
        "ranking_value": getattr(row, "ranking_value", None),
        "score_version": score_version,
        "ranker_version": str(getattr(row, "ranker_version", "") or RANKER_VERSION_V1),
        "scoring_config_version": str(getattr(row, "scoring_config_version", "") or ""),
        "selected_for_portfolio": bool(getattr(row, "selected_for_portfolio", False)),
        "decision_snapshot_id": getattr(row, "decision_snapshot_id", None),
        "selection_metadata": (getattr(row, "selection_metadata", None)
                               if isinstance(getattr(row, "selection_metadata", None), dict) else {}),
        "confidence": round(float(row.confidence or 0), 3) if row.confidence is not None else None,
        "status": row.status or "inactive",
        "action": action,
        "action_label": action_label,
        "signal": row.signal or "",
        "reason": row.reason or "",
        "evidence": row.evidence or [],
        "holding_days": int(row.holding_days or 3),
        "entry_low": row.entry_low,
        "entry_high": row.entry_high,
        "stop_loss": row.stop_loss,
        "target_price": row.target_price,
        "invalidation": row.invalidation or "",
        "plan_quality": int(row.plan_quality or 0),
        "source_agent": row.source_agent or "",
        "source_suggestion_id": row.source_suggestion_id,
        "source_candidate_id": row.source_candidate_id,
        "trace_id": row.trace_id or "",
        "is_holding_snapshot": bool(row.is_holding_snapshot),
        "context_quality_score": row.context_quality_score,
        "score_breakdown": score_breakdown,
        "market_regime": market_regime,
        "cross_feature": cross_feature,
        "news_metric": news_metric,
        "constrained": (bool(getattr(row, "constrained", False)) if getattr(row, "ranker_version", None) == RANKER_VERSION_V2 else constrained),
        "constraint_reasons": [str(x) for x in stored_reasons if str(x).strip()],
        "payload": payload if include_payload else {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


CAPTURE_FACTOR_FIELDS = (
    "alpha_score",
    "catalyst_score",
    "quality_score",
    "risk_penalty",
    "crowd_penalty",
    "source_bonus",
)


def _capture_ranking_decisions(db, *, rows: list, session_date: str,
                              candidates: list[EntryCandidate] | None = None,
                              cross_features: dict | None = None,
                              candidate_limit: int | None = None) -> dict:
    """Append-only decision capture (PR-0A): immutable items, UI projection stays separate.

    The capture stores the factor values that actually fed each decision, so calibration
    reads archived decision inputs instead of mutable StrategyFactorSnapshot projections.
    Provenance stays UNVERIFIABLE until the RC-D evaluator labels the population, which
    keeps every market ineligible (and therefore FROZEN/SHADOW) without weakening the gate.
    """
    db.flush()
    captured_at = utc_now()
    by_market: dict[str, list[dict]] = {}
    configs_by_market: dict[str, list[dict]] = {}
    projection_rows_by_market: dict[str, list] = {}
    for row in rows:
        market = (row.stock_market or "CN").strip().upper() or "CN"
        symbol = (row.stock_symbol or "").strip().upper()
        instrument_id = (row.instrument_id or "").strip().upper()
        if market == "TW":
            # Never manufacture a TW: prefix in front of an already venue-qualified
            # symbol. A bare/unclassified Taiwan symbol is not an immutable identity.
            if not instrument_id and symbol.split(":", 1)[0] in {"TWSE", "TPEX"}:
                instrument_id = symbol
            if not instrument_id or instrument_id.split(":", 1)[0] not in {"TWSE", "TPEX"}:
                # Persist an explicit unresolved identity for audit. This marker is
                # never eligible for Taiwan selection and does not guess a venue.
                unresolved = symbol or "NO-SYMBOL"
                source_id = row.source_candidate_id if row.source_candidate_id is not None else row.id
                instrument_id = f"UNRESOLVED-TW:{unresolved}:{source_id or 'NO-ID'}"
        else:
            if not instrument_id:
                instrument_id = f"{market}:{symbol}" if symbol else f"UNRESOLVED-{market}:{row.id or 'NO-ID'}"
        payload = row.payload if isinstance(row.payload, dict) else {}
        breakdown = payload.get("score_breakdown") if isinstance(
            payload.get("score_breakdown"), dict
        ) else {}
        # A factor the ranker did not emit stays missing; it is never a zero sample.
        factors = {
            code: _finite_float(breakdown.get(code))
            for code in CAPTURE_FACTOR_FIELDS
        }
        by_market.setdefault(market, []).append({
            "instrument_id": instrument_id,
            "symbol": row.stock_symbol or instrument_id,
            "strategy_code": row.strategy_code,
            "raw_factor_values": factors,
            "ranking_value": breakdown.get("ranking_value"),
            "ranking_inputs": {
                "candidate_score": _finite_float(breakdown.get("base_score")),
                "risk_level": row.risk_level,
                "cross_section_features": payload.get("cross_feature") or {},
                "raw_factor_values": factors,
                "score_breakdown": breakdown,
                "strategy_weight": payload.get("strategy_weight"),
                "factor_weights": payload.get("factor_weights") or {},
                "score_version": SCORE_VERSION_V2,
                "missing_feature_policy": MISSING_FEATURE_POLICY,
            },
            "primary_horizon_sessions": int(row.holding_days or 0) or None,
            "candidate_id": row.source_candidate_id,
            "signal_run_id": int(row.id) if row.id is not None else None,
            "instrument_group": row.instrument_group or "UNKNOWN",
            "instrument_authority": row.instrument_authority or {},
            "scan_run_ids": list(row.scan_run_ids or []),
            "scan_run_refs": list(row.scan_run_refs or []),
            "entry_price": row.entry_high or row.entry_low,
            "entry_low": row.entry_low,
            "entry_high": row.entry_high,
            "stop_loss": row.stop_loss,
            "target_price": row.target_price,
            "action": row.action or "watch",
            "is_holding_snapshot": bool(row.is_holding_snapshot),
            "candidate_source": row.source_pool or "",
            "score_version": SCORE_VERSION_V2,
            "regime_multiplier": breakdown.get("regime_multiplier", 1.0),
            "regime": (payload.get("market_regime") or {}).get("regime") or "default",
            "eligibility": {"eligible": row.status == "active", "status": row.status,
                            "action": row.action, "source_agent": row.source_agent,
                            "source_suggestion_id": row.source_suggestion_id,
                            "trace_id": row.trace_id, "source_meta": payload.get("source_meta") or {}},
        })
        configs_by_market.setdefault(market, []).append({
            "instrument_id": instrument_id, "strategy_code": row.strategy_code,
            "strategy_weight": payload.get("strategy_weight"),
            "factor_weights": payload.get("factor_weights"),
        })
        projection_rows_by_market.setdefault(market, []).append(row)
    capture_refs: dict[str, dict] = {}
    for market, decisions in sorted(by_market.items()):
        # Capture identity is run-scoped: one market/session keeps one capture, but a
        # same-day refresh or a correction can no longer reuse a fixed day-level id and
        # silently inherit another execution's archived decision set.
        run_ids = sorted({int(d["signal_run_id"]) for d in decisions if d["signal_run_id"] is not None})
        scan_run_ids = sorted({
            int(run_id)
            for decision in decisions
            for run_id in (decision.get("scan_run_ids") or [])
            if run_id is not None
        })
        scan_refs_by_item = {
            decision["instrument_id"] + "|" + str(decision["strategy_code"]): decision.get("scan_run_refs") or []
            for decision in decisions
        }
        run_token = (hashlib.sha256(str(run_ids).encode("utf-8")).hexdigest()[:12]
                     if run_ids else "no-runs")
        capture_id = f"decision-{market}-{session_date}-{run_token}"
        # Seal the catalog authority at decision time so a later catalog edit cannot move
        # this cohort in or out; the selector reads this, not the live catalog.
        catalog_authority = get_primary_horizon_sessions()
        market_candidates = [
            candidate for candidate in (candidates or [])
            if (candidate.stock_market or "CN").strip().upper() == market
        ]
        reference_items: list[dict] = []
        for candidate in market_candidates:
            symbol = (candidate.stock_symbol or "").strip().upper()
            instrument_id = (candidate.instrument_id or "").strip().upper()
            if market == "TW":
                if not instrument_id and symbol.split(":", 1)[0] in {"TWSE", "TPEX"}:
                    instrument_id = symbol
                if not instrument_id or instrument_id.split(":", 1)[0] not in {"TWSE", "TPEX"}:
                    instrument_id = f"UNRESOLVED-TW:{symbol or 'NO-SYMBOL'}:{candidate.id or 'NO-ID'}"
            else:
                instrument_id = instrument_id or (f"{market}:{symbol}" if symbol
                                                    else f"UNRESOLVED-{market}:{candidate.id or 'NO-ID'}")
            feature_item = (cross_features or {}).get(int(candidate.id)) if candidate.id is not None else None
            feature_values = feature_item.get("feature_values") if isinstance(feature_item, dict) else None
            reference_items.append({
                "candidate_id": int(candidate.id) if candidate.id is not None else None,
                "instrument_id": instrument_id,
                "symbol": candidate.stock_symbol or instrument_id,
                "instrument_group": candidate.instrument_group or "UNKNOWN",
                "instrument_authority": candidate.instrument_authority or {},
                "candidate_score": _finite_float(candidate.score),
                "status": candidate.status or "inactive",
                "action": candidate.action or "watch",
                "candidate_source": candidate.candidate_source or "",
                "feature_values": feature_values or {
                    "candidate_score": _finite_float(candidate.score),
                    "change_pct": None, "turnover": None, "volume_ratio": None,
                },
                "missing_feature_policy": MISSING_FEATURE_POLICY,
            })
        reference_items.sort(key=lambda item: (
            item["instrument_id"], item["instrument_group"],
            item["candidate_id"] if item["candidate_id"] is not None else -1,
        ))
        ranking_reference = {
            "reference_scope": "bounded-entry-candidate-universe-before-constraints",
            "reference_limit": int(candidate_limit) if candidate_limit is not None else None,
            "ordering": ["score_desc", "market", "instrument_group", "instrument_id",
                         "stock_symbol", "candidate_id"],
            "includes_inactive_and_no_strategy_candidates": True,
            "missing_feature_policy": MISSING_FEATURE_POLICY,
            "candidate_count": len(reference_items),
            "items": reference_items,
        }
        try:
            # Newly created signal rows are still pending: flush first so row.id exists and
            # the capture can carry a real signal reference instead of "no-runs".
            db.flush()
            scoring_config_payload = {
                "ranker_version": RANKER_VERSION_V2,
                "display_mapping": {"kind": "sigmoid", "center": 100.0, "scale": 20.0,
                                    "round_at_serialization": 2},
                "tie_policy": "average-rank-position; position-1-is-best",
                "missing_feature_policy": MISSING_FEATURE_POLICY,
                "missing_candidate_score_policy": "capture-null; ranking-input-invalid; exclude-from-selection",
                "invalid_ranking_input_policy": "capture-null; do-not-rank; exclude-from-selection",
                "candidate_eligibility_gate": {
                    "market_scan_or_mixed_min_score": 62,
                    "watchlist_min_score": 55,
                    "requires_action": ["buy", "add"],
                    "requires_plan_quality_at_least": 90,
                    "rank_score_not_used": True,
                    "tw_unheld_requires_instrument_group": ["EQUITY", "ETF"],
                },
                "consumer_thresholds": {"dashboard_min_score": 55,
                                         "opportunities_min_score": 70,
                                         "rank_tier_color": 80,
                                         "position_weight": {"85": 0.25, "75": 0.18,
                                                             "65": 0.12, "else": 0.08}},
                "selection_policy": {
                    "tw": {"constraint_unit": "INSTRUMENT", "equity_cap": TW_INSTRUMENT_CAP,
                           "high_risk_share": TW_HIGH_RISK_SHARE,
                           "single_strategy_share": TW_SINGLE_STRATEGY_SHARE,
                           "representative": ["active", "buy/add", "ranking_value_desc",
                                              "strategy_code_asc", "instrument_id_asc",
                                              "decision_snapshot_id_asc"],
                           "quota_rounding": "floor-final-instrument-count; minimum-one",
                           "pruning": "weakest-violating-instrument; recompute; no-replenishment",
                           "held_management": "excluded-from-unheld-quotas",
                           "etf": {"unit": "ROW", "cap": 20, "high_risk_share": 0.32,
                                   "strategy_share": 0.42, "rounding": "python-round-compatible"}},
                    "other_markets": {"unit": "ROW", "legacy_caps_and_rounding": True,
                                      "caps": MAX_UNHELD_ACTIVE_BY_MARKET,
                                      "risk_shares": MAX_HIGH_RISK_RATIO_BY_MARKET,
                                      "strategy_shares": MAX_SINGLE_STRATEGY_SHARE_BY_MARKET},
                    "reference_scope": "bounded-entry-candidate-universe-before-constraints",
                    "reference_includes_inactive_and_no_strategy_candidates": True,
                    "candidate_order": ["score_desc", "market", "instrument_group",
                                        "instrument_id", "stock_symbol", "candidate_id"],
                },
                "effective_weights": sorted(configs_by_market[market],
                                             key=lambda c: (c["instrument_id"], c["strategy_code"])),
                "catalog_authority": catalog_authority,
            }
            result = capture_decision_snapshot(
                db,
                capture_id=capture_id,
                market=market,
                session_date=session_date,
                decisions=decisions,
                ranker_version=RANKER_VERSION_V2,
                evaluation_version=EVALUATOR_SIGNAL_FORWARD_V2,
                population=POPULATION_SIGNAL_FORWARD_V2,
                catalog_authority=catalog_authority,
                decision_at_utc=captured_at,
                captured_at_utc=captured_at,
                scan_run_ids=scan_run_ids,
                scan_run_refs_by_item=scan_refs_by_item,
                scoring_config_payload=scoring_config_payload,
                ranking_reference=ranking_reference,
                commit=False,
            )
        except Exception:
            # capture 是 evidence 的 side channel: signal refresh must not fail because of it.
            logger.exception(
                "[策略層] decision capture 失敗: market=%s session=%s",
                market,
                session_date,
            )
            continue
        snapshot_id = int(result["snapshot_id"])
        items_by_signal = {
            int(item.signal_run_id): item
            for item in db.query(RankingSnapshotItem)
            .filter(RankingSnapshotItem.ranking_snapshot_id == snapshot_id)
            .all()
            if item.signal_run_id is not None
        }
        config_version = str(result.get("scoring_config_version") or "")
        for row in projection_rows_by_market.get(market, []):
            item = items_by_signal.get(int(row.id)) if row.id is not None else None
            row.ranker_version = RANKER_VERSION_V2
            row.score_version = SCORE_VERSION_V2
            row.scoring_config_version = config_version
            row.decision_snapshot_id = item.decision_snapshot_id if item else None
        capture_refs[market] = {
            "capture_id": str(result.get("capture_id") or capture_id),
            "snapshot_id": snapshot_id,
            "ranker_version": RANKER_VERSION_V2,
            "scoring_config_version": config_version,
            "ranking_reference_hash": str(result.get("ranking_reference_hash") or ""),
            "decision_ids_by_signal": {
                signal_id: item.decision_snapshot_id
                for signal_id, item in items_by_signal.items()
            },
        }
        logger.info(
            "[策略層] decision capture: market=%s session=%s capture=%s written=%s reused=%s conflicts=%s items=%s",
            market,
            session_date,
            result["capture_id"],
            result["written"],
            result["reused"],
            result["conflicts"],
            result["item_count"],
        )
    return capture_refs


def refresh_strategy_signals(
    *,
    snapshot_date: str = "",
    rebuild_candidates: bool = False,
    max_inputs: int = 500,
    market_scan_limit: int = 80,
    max_kline_symbols: int = 72,
    limit_candidates: int = 2000,
) -> dict:
    ensure_strategy_catalog()
    if rebuild_candidates:
        refresh_entry_candidates(
            max_inputs=max_inputs,
            snapshot_date=snapshot_date or None,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
        )

    db = SessionLocal()
    try:
        snapshot = (snapshot_date or "").strip()
        if not snapshot:
            latest = (
                db.query(EntryCandidate.snapshot_date)
                .order_by(EntryCandidate.snapshot_date.desc())
                .first()
            )
            snapshot = latest[0] if latest else ""
        if not snapshot:
            return {"snapshot_date": "", "count": 0, "items": []}

        candidates = (
            db.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == snapshot, EntryCandidate.stock_market.in_(enabled_market_codes()))
            .order_by(
                EntryCandidate.score.desc(),
                EntryCandidate.stock_market.asc(),
                EntryCandidate.instrument_group.asc(),
                EntryCandidate.instrument_id.asc(),
                EntryCandidate.stock_symbol.asc(),
                EntryCandidate.id.asc(),
            )
            .limit(max(20, int(limit_candidates)))
            .all()
        )
        if not candidates:
            db.query(StrategySignalRun).filter(
                StrategySignalRun.snapshot_date == snapshot
            ).delete(synchronize_session=False)
            db.commit()
            return {"snapshot_date": snapshot, "count": 0, "items": []}

        profile_map = get_strategy_profile_map()
        regime_rows = _upsert_market_regime_snapshots(
            db=db,
            snapshot=snapshot,
            candidates=candidates,
        )
        cross_features = _build_cross_section_features(candidates)
        news_metrics = _load_news_metrics(
            db=db,
            candidates=candidates,
            lookback_hours=72,
            max_rows=5000,
        )
        existing_rows = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.snapshot_date == snapshot)
            .all()
        )
        # Candidate IDs can be reused when a day's candidate snapshot is rebuilt.
        # A signal belongs to the instrument as well as to the numeric candidate ID.
        existing: dict[tuple[int, str, str, str], StrategySignalRun] = {}
        for row in existing_rows:
            cand_id = row.source_candidate_id
            code = row.strategy_code
            if cand_id is None:
                continue
            existing[(int(cand_id), str(code or ""), row.stock_symbol, row.stock_market)] = row

        weight_cache: dict[str, dict[str, float]] = {}
        factor_weight_cache: dict[str, dict[str, float]] = {}
        reused_ids: set[int] = set()
        touched_rows: list[StrategySignalRun] = []

        for c in candidates:
            market = (c.stock_market or "CN").strip().upper() or "CN"
            weights = weight_cache.get(market)
            if weights is None:
                weights = get_effective_weight_map(market=market, regime="default")
                weight_cache[market] = weights
            factor_weights = factor_weight_cache.get(market)
            if factor_weights is None:
                factor_weights = get_factor_weights(market, db=db)
                factor_weight_cache[market] = factor_weights
            codes = _strategy_codes_for_candidate(c)
            for code in codes:
                profile = profile_map.get(code) or profile_map.get("watchlist_agent") or {}
                weight = float(weights.get(code, profile.get("default_weight", 1.0)))
                risk_level = (profile.get("risk_level") or "medium").strip() or "medium"
                strategy_name = profile.get("name") or code
                strategy_version = profile.get("version") or "v1"
                horizon_days = 3
                params = profile.get("params") or {}
                if isinstance(params, dict):
                    horizon_days = max(1, int(params.get("horizon_days", 3) or 3))

                regime_info = regime_rows.get(market) or {
                    "regime": "neutral",
                    "confidence": 0.0,
                }
                symbol_key = (c.stock_symbol or "").strip().upper()
                normalized_news_metric = _normalize_news_metric(news_metrics.get(symbol_key))
                score_breakdown = _compute_factor_breakdown(
                    row=c,
                    strategy_code=code,
                    weight=weight,
                    risk_level=risk_level,
                    regime_info=regime_info,
                    cross_feature=cross_features.get(int(c.id)) if c.id is not None else None,
                    news_metric=normalized_news_metric,
                    factor_weights=factor_weights,
                )
                rank_value = score_breakdown.get("ranking_value")
                rank_score = score_breakdown.get("rank_score")
                rank_score = float(rank_score) if rank_score is not None else None
                confidence = c.confidence
                cmeta = c.meta if isinstance(c.meta, dict) else {}
                source_meta = cmeta.get("source_meta") if isinstance(cmeta.get("source_meta"), dict) else {}
                context_quality_score = _safe_float(source_meta.get("context_quality_score"))
                compact_source_meta = _compact_source_meta(source_meta)
                action = (c.action or "watch").strip().lower() or "watch"
                action_label = (c.action_label or "觀望").strip() or "觀望"
                if bool(c.is_holding_snapshot):
                    if action == "buy":
                        action = "add"
                        action_label = "準備加碼"
                else:
                    if action == "add":
                        action = "buy"
                        action_label = "建倉"
                    elif action == "hold":
                        action_label = "觀望"
                payload = {
                    "entry_candidate_id": c.id,
                    "entry_candidate_snapshot": c.snapshot_date,
                    "strategy_tags": c.strategy_tags or [],
                    "strategy_weight": weight,
                    "factor_weights": dict(factor_weights),
                    "source_meta": compact_source_meta,
                    "score_breakdown": score_breakdown,
                    "market_regime": {
                        "regime": regime_info.get("regime") or "neutral",
                        "regime_label": regime_info.get("regime_label") or _regime_label(regime_info.get("regime") or "neutral"),
                        "confidence": regime_info.get("confidence") or 0.0,
                        "regime_score": regime_info.get("regime_score") or 0.0,
                    },
                    "cross_feature": cross_features.get(int(c.id)) if c.id is not None else {},
                    "news_metric": normalized_news_metric,
                }
                key = (int(c.id), str(code), c.stock_symbol, market)
                row = existing.get(key)
                if not row:
                    row = StrategySignalRun(
                        snapshot_date=snapshot,
                        stock_symbol=c.stock_symbol,
                        stock_market=market,
                        stock_name=c.stock_name or c.stock_symbol,
                        strategy_code=code,
                        source_candidate_id=c.id,
                    )
                    db.add(row)
                    existing[key] = row
                elif row.id is not None:
                    reused_ids.add(int(row.id))

                row.stock_symbol = c.stock_symbol
                row.stock_market = market
                row.stock_name = c.stock_name or c.stock_symbol
                row.strategy_name = strategy_name
                row.strategy_version = strategy_version
                row.risk_level = risk_level
                row.source_pool = c.candidate_source or "watchlist"
                row.score = _finite_float(c.score) or 0.0
                # rank_score is a legacy NOT NULL projection. The v2 serializer reads
                # ranking_value and emits NULL when its source input was missing.
                row.rank_score = float(rank_score) if rank_score is not None else 0.0
                row.ranking_value = rank_value
                row.score_version = SCORE_VERSION_V2
                row.ranker_version = RANKER_VERSION_V2
                row.scoring_config_version = ""
                row.decision_snapshot_id = None
                row.confidence = _finite_float(confidence)
                row.status = c.status or "inactive"
                row.action = action
                row.action_label = action_label
                row.signal = c.signal or ""
                row.reason = c.reason or ""
                row.evidence = to_jsonable(c.evidence or [])
                row.holding_days = int(horizon_days)
                row.entry_low = c.entry_low
                row.entry_high = c.entry_high
                row.stop_loss = c.stop_loss
                row.target_price = c.target_price
                row.invalidation = c.invalidation or ""
                row.plan_quality = int(c.plan_quality or 0)
                row.source_agent = c.source_agent or ""
                row.source_suggestion_id = c.source_suggestion_id
                row.trace_id = c.source_trace_id or ""
                row.is_holding_snapshot = bool(c.is_holding_snapshot)
                row.context_quality_score = context_quality_score
                row.instrument_id = c.instrument_id
                row.instrument_group = c.instrument_group or "UNKNOWN"
                row.instrument_authority = c.instrument_authority or {}
                row.scan_run_id = c.scan_run_id
                row.scan_run_ids = list(c.scan_run_ids or [])
                row.scan_run_refs = list(c.scan_run_refs or [])
                row.payload = to_jsonable(payload)
                row.updated_at = utc_now()
                touched_rows.append(row)

        # Seal the pre-constraint reference set and eligibility before projection caps.
        capture_refs = _capture_ranking_decisions(
            db, rows=touched_rows, session_date=snapshot, candidates=candidates,
            cross_features=cross_features, candidate_limit=max(20, int(limit_candidates)),
        )
        constraint_stats = _apply_portfolio_constraints(
            rows=touched_rows, db=db, capture_refs=capture_refs, snapshot_date=snapshot,
        )
        if constraint_stats.get("demoted", 0) > 0:
            logger.info(
                "[策略層] 組合約束生效: snapshot=%s demoted=%s details=%s",
                snapshot,
                constraint_stats.get("demoted", 0),
                constraint_stats.get("by_reason", {}),
            )

        # Remove stale strategy rows for same candidate snapshot when strategy mapping changed.
        stale_ids = [int(row.id) for row in existing_rows
                     if row.id is not None and int(row.id) not in reused_ids]
        if stale_ids:
            db.query(StrategySignalRun).filter(
                StrategySignalRun.id.in_(stale_ids)
            ).delete(synchronize_session=False)

        db.commit()

        rows = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.snapshot_date == snapshot)
            .order_by(StrategySignalRun.ranker_version.desc(), StrategySignalRun.ranking_value.desc(),
                      StrategySignalRun.stock_market.asc(), StrategySignalRun.instrument_id.asc(),
                      StrategySignalRun.stock_symbol.asc(), StrategySignalRun.strategy_code.asc(),
                      StrategySignalRun.id.asc())
            .all()
        )
        _sync_factor_and_risk_snapshots(
            db=db,
            snapshot=snapshot,
            signals=rows,
        )
        db.commit()
        factor_map: dict[int, StrategyFactorSnapshot] = {}
        run_ids = [int(x.id) for x in rows if x.id is not None]
        if run_ids:
            factors = (
                db.query(StrategyFactorSnapshot)
                .filter(
                    StrategyFactorSnapshot.snapshot_date == snapshot,
                    StrategyFactorSnapshot.signal_run_id.in_(run_ids),
                )
                .all()
            )
            factor_map = {int(f.signal_run_id): f for f in factors if f.signal_run_id is not None}
        items = [
            _format_signal(
                x,
                include_payload=False,
                factor_snapshot=factor_map.get(int(x.id)) if (x.id is not None) else None,
            )
            for x in rows[:3000]
        ]
        return {
            "snapshot_date": snapshot,
            "count": len(rows),
            "items": items,
            "constraints": constraint_stats,
        }
    finally:
        db.close()


def list_strategy_signals(
    *,
    market: str = "",
    status: str = "all",
    min_score: float = 0,
    limit: int = 50,
    snapshot_date: str = "",
    source_pool: str = "",
    holding: str = "",
    strategy_code: str = "",
    risk_level: str = "",
    instrument_group: str = "",
    include_payload: bool = False,
) -> dict:
    ensure_strategy_catalog()
    db = SessionLocal()
    try:
        snapshot = (snapshot_date or "").strip()
        if not snapshot:
            latest = (
                db.query(StrategySignalRun.snapshot_date)
                .order_by(StrategySignalRun.snapshot_date.desc())
                .first()
            )
            snapshot = latest[0] if latest else ""
        if not snapshot:
            return {"snapshot_date": "", "count": 0, "items": []}

        q = db.query(StrategySignalRun).filter(
            StrategySignalRun.snapshot_date == snapshot,
            _current_candidate_identity_filter(),
        )
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(StrategySignalRun.stock_market == mkt)
        instrument_kind = (instrument_group or "").strip().upper()
        if instrument_kind == "ORDINARY":
            q = q.filter(or_(
                StrategySignalRun.stock_market != "TW",
                StrategySignalRun.instrument_group == "EQUITY",
            ))
        elif instrument_kind:
            q = q.filter(StrategySignalRun.instrument_group == instrument_kind)
        st = (status or "").strip().lower()
        if st and st != "all":
            q = q.filter(StrategySignalRun.status == st)
        src = (source_pool or "").strip().lower()
        if src and src != "all":
            if src == "market_scan":
                q = q.filter(StrategySignalRun.source_pool.in_(("market_scan", "mixed")))
            elif src == "watchlist":
                q = q.filter(StrategySignalRun.source_pool == "watchlist")
            else:
                q = q.filter(StrategySignalRun.source_pool == src)
        h = (holding or "").strip().lower()
        if h == "held":
            q = q.filter(StrategySignalRun.is_holding_snapshot.is_(True))
        elif h == "unheld":
            q = q.filter(StrategySignalRun.is_holding_snapshot.is_(False))
        scode = (strategy_code or "").strip()
        if scode:
            q = q.filter(StrategySignalRun.strategy_code == scode)
        r = (risk_level or "").strip().lower()

        # Fetch the full filtered cohort before selecting instrument representatives;
        # a SQL row limit here could let duplicate strategies consume the page.
        rows = q.order_by(
            StrategySignalRun.stock_market.asc(),
            StrategySignalRun.instrument_id.asc(),
            StrategySignalRun.stock_symbol.asc(),
            StrategySignalRun.strategy_code.asc(),
            StrategySignalRun.id.asc(),
        ).all()
        factor_map: dict[int, StrategyFactorSnapshot] = {}
        if rows and not include_payload:
            ids = [int(x.id) for x in rows if x.id is not None]
            if ids:
                factors = (
                    db.query(StrategyFactorSnapshot)
                    .filter(
                        StrategyFactorSnapshot.snapshot_date == snapshot,
                        StrategyFactorSnapshot.signal_run_id.in_(ids),
                    )
                    .all()
                )
                factor_map = {int(f.signal_run_id): f for f in factors if f.signal_run_id is not None}
        items = [
            _format_signal(
                x,
                include_payload=include_payload,
                factor_snapshot=factor_map.get(int(x.id)) if (x.id is not None) else None,
            )
            for x in rows
        ]
        # A response is a single score scale. Use v2 if this filtered cohort contains
        # it; otherwise retain bounded legacy compatibility for that query.
        raw_scores = {row.id: row.rank_score for row in rows}
        v2_items = [item for item in items if item.get("ranker_version") == RANKER_VERSION_V2]
        score_cohort = v2_items if v2_items else [
            item for item in items if item.get("ranker_version") != RANKER_VERSION_V2
        ]
        legacy_omitted_count = len(items) - len(score_cohort)
        score_version = RANKER_VERSION_V2 if v2_items else RANKER_VERSION_V1
        if h == "held" and float(min_score or 0) <= 0:
            score_cohort = items
            legacy_omitted_count = 0
            score_version = "mixed" if v2_items and len(v2_items) != len(items) else score_version
        if float(min_score or 0) > 0:
            threshold_cohort = [
                item for item in score_cohort
                if item.get("rank_score") is not None
                and float(raw_scores[item["id"]]) >= float(min_score)
            ]
        else:
            threshold_cohort = score_cohort
        for item in threshold_cohort:
            metadata = item.get("selection_metadata") or {}
            item["strategy_risk_level"] = item.get("risk_level")
            item["risk_level"] = metadata.get("group_risk_level") or item.get("risk_level")
        instrument_groups = group_instrument_rows(threshold_cohort)
        if r and r != "all":
            instrument_groups = [pair for pair in instrument_groups if
                                 max(pair[1], key=lambda x: _risk_rank(x.get("risk_level")))["risk_level"] == r]
        selected_groups = instrument_groups[:max(1, int(limit))]
        representatives = []
        for representative, members in selected_groups:
            item = dict(representative)
            item["risk_level"] = max(members, key=lambda x: _risk_rank(x.get("risk_level")))["risk_level"]
            item["risk_level_label"] = _risk_label(item["risk_level"])
            item["strategy_count"] = len(members)
            item["strategy_codes"] = sorted({str(member.get("strategy_code") or "")
                                               for member in members})
            representatives.append(item)
        return {"snapshot_date": snapshot, "count": len(representatives),
                "ranker_version": score_version, "legacy_omitted_count": legacy_omitted_count,
                "items": representatives}
    finally:
        db.close()


def _pending_due_horizons(
    *,
    signal_id: int,
    snapshot_day: date,
    today: date,
    horizons: tuple[int, ...] | list[int],
    existing: set[tuple[int, int]],
) -> tuple[list[int], int]:
    """篩出尚未落庫且已經到期的 horizon，避免無意義載入 K 線。"""
    pending: list[int] = []
    skipped_not_due = 0
    for horizon in horizons:
        h = max(1, int(horizon))
        if (signal_id, h) in existing:
            continue
        if snapshot_day + timedelta(days=h) > today:
            skipped_not_due += 1
            continue
        pending.append(h)
    return pending, skipped_not_due


def evaluate_strategy_outcomes(
    *,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    snapshot_days: int = 60,
    limit: int = 800,
) -> dict:
    stats = {
        "total_signals": 0,
        "eligible": 0,
        "evaluated": 0,
        "skipped_not_due": 0,
        "skipped_no_price": 0,
        "skipped_no_base_price": 0,
    }
    safe_horizons = sorted({max(1, int(h)) for h in horizons if int(h) > 0})
    if not safe_horizons:
        safe_horizons = [1, 3, 5]

    db = SessionLocal()
    try:
        cutoff = date.today() - timedelta(days=max(7, int(snapshot_days)))
        signals = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date >= cutoff.strftime("%Y-%m-%d"),
                StrategySignalRun.status.in_(("active", "inactive")),
                StrategySignalRun.action.in_(("buy", "add", "hold", "watch")),
            )
            .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.rank_score.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        stats["total_signals"] = len(signals)
        if not signals:
            return stats

        existing_rows = (
            db.query(StrategyOutcome.signal_run_id, StrategyOutcome.horizon_days)
            .filter(StrategyOutcome.signal_run_id.in_([s.id for s in signals]))
            .all()
        )
        existing = {(int(x), int(y)) for x, y in existing_rows}

        today = date.today()
        kline_cache: dict[tuple[str, str], list] = {}
        pending = 0  # 分批提交計數,縮短寫事務視窗

        for s in signals:
            snap_day = _parse_day(s.snapshot_date)
            if snap_day is None:
                continue
            pending_horizons, skipped_not_due = _pending_due_horizons(
                signal_id=int(s.id),
                snapshot_day=snap_day,
                today=today,
                horizons=safe_horizons,
                existing=existing,
            )
            stats["skipped_not_due"] += skipped_not_due
            # 所有 horizon 都已評估或尚未到期時，不需要聯網取該標的 K 線。
            if not pending_horizons:
                continue
            key = (
                (s.stock_symbol or "").strip(),
                (s.stock_market or "CN").strip().upper(),
            )
            if key not in kline_cache:
                try:
                    lookback = max(120, (today - snap_day).days + 30)
                    kline_cache[key] = KlineCollector(_to_market(key[1])).get_klines(
                        key[0], days=min(lookback, 600)
                    )
                except Exception:
                    kline_cache[key] = []
            klines = kline_cache[key]

            for horizon in pending_horizons:
                target_day = snap_day + timedelta(days=horizon)
                stats["eligible"] += 1
                outcome_price = _pick_close_on_or_before(klines, target_day)
                if outcome_price is None:
                    stats["skipped_no_price"] += 1
                    continue
                base_price = None
                if s.entry_low is not None and s.entry_high is not None:
                    base_price = (float(s.entry_low) + float(s.entry_high)) / 2
                elif s.entry_high is not None:
                    base_price = float(s.entry_high)
                elif s.entry_low is not None:
                    base_price = float(s.entry_low)
                if base_price is None:
                    payload = s.payload if isinstance(s.payload, dict) else {}
                    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
                    quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
                    base_price = _safe_float(quote.get("current_price"))
                if base_price is None:
                    base_price = _pick_close_on_or_before(klines, snap_day)
                if base_price is None or base_price <= 0:
                    stats["skipped_no_base_price"] += 1
                    status = "no_base_price"
                    ret = None
                else:
                    ret = (outcome_price - base_price) / base_price * 100.0
                    if s.target_price is not None and outcome_price >= float(s.target_price):
                        status = "hit_target"
                    elif s.stop_loss is not None and outcome_price <= float(s.stop_loss):
                        status = "hit_stop"
                    else:
                        status = "evaluated"
                hit_target = (
                    bool(s.target_price is not None and outcome_price >= float(s.target_price))
                    if status != "no_base_price"
                    else None
                )
                hit_stop = (
                    bool(s.stop_loss is not None and outcome_price <= float(s.stop_loss))
                    if status != "no_base_price"
                    else None
                )
                db.add(
                    StrategyOutcome(
                        signal_run_id=s.id,
                        strategy_code=s.strategy_code,
                        snapshot_date=s.snapshot_date,
                        stock_symbol=s.stock_symbol,
                        stock_market=s.stock_market,
                        source_pool=s.source_pool or "watchlist",
                        horizon_days=horizon,
                        target_date=target_day.strftime("%Y-%m-%d"),
                        base_price=base_price,
                        outcome_price=outcome_price,
                        outcome_return_pct=ret,
                        hit_target=hit_target,
                        hit_stop=hit_stop,
                        outcome_status=status,
                        meta=to_jsonable(
                            {
                                "rank_score": float(s.rank_score or 0),
                                "action": s.action or "",
                                "action_label": s.action_label or "",
                            }
                        ),
                        evaluated_at=utc_now(),
                    )
                )
                stats["evaluated"] += 1
                existing.add((s.id, horizon))
                pending += 1

            # 分批提交:累計到閾值即落盤,縮短寫事務,避免與 60s 排程器併發寫長時間持鎖
            if pending >= 50:
                db.commit()
                pending = 0

        db.commit()
        return stats
    except Exception as e:
        db.rollback()
        logger.warning(f"策略後驗評估失敗: {e}")
        return stats
    finally:
        db.close()


def _aggregate_recent_outcomes(*, db, days: int):
    since = utc_now() - timedelta(days=max(1, int(days)))
    rows = (
        db.query(
            StrategyOutcome.strategy_code,
            StrategyOutcome.stock_market,
            func.count(StrategyOutcome.id).label("total"),
            func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
            func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
        )
        .filter(
            StrategyOutcome.created_at >= since,
            StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
        )
        .group_by(StrategyOutcome.strategy_code, StrategyOutcome.stock_market)
        .all()
    )
    by_pair: dict[tuple[str, str], dict] = {}
    by_strategy_all: dict[str, dict] = {}
    for code, market, total, wins, avg_ret in rows:
        c = (code or "").strip()
        m = (market or "ALL").strip().upper() or "ALL"
        t = int(total or 0)
        w = int(wins or 0)
        a = float(avg_ret or 0.0)
        by_pair[(c, m)] = {"sample_size": t, "wins": w, "avg_return_pct": a}
        x = by_strategy_all.setdefault(c, {"sample_size": 0, "wins": 0, "ret_sum": 0.0})
        x["sample_size"] += t
        x["wins"] += w
        x["ret_sum"] += a * t
    for c, x in by_strategy_all.items():
        x["avg_return_pct"] = (x["ret_sum"] / x["sample_size"]) if x["sample_size"] > 0 else 0.0
    return by_pair, by_strategy_all


def rebalance_strategy_weights(
    *,
    window_days: int = 45,
    min_samples: int = 8,
    alpha: float = 0.35,
    regime: str = "default",
) -> dict:
    ensure_strategy_catalog()
    reg = (regime or "default").strip() or "default"
    alpha = _clamp(float(alpha or 0.35), 0.05, 0.95)
    window_days = max(7, min(int(window_days or 45), 365))
    min_samples = max(3, min(int(min_samples or 8), 200))

    db = SessionLocal()
    try:
        catalogs = list_strategy_catalog(enabled_only=True)
        changed = 0
        checked = 0
        skipped_low_sample = 0
        rows_changed: list[dict] = []
        statuses: dict[str, str] = {}
        modes: dict[str, str] = {}

        for market in ("ALL", *enabled_market_codes()):
            targets = [f"{c['code']}|{reg}" for c in catalogs]
            batch = plan_calibration_batch(db, kind="strategy", market=market,
                                          targets=targets, regime=reg)
            proposals: dict[str, float] = {}
            reasons: dict[str, str] = {}
            for c in catalogs:
                code = c["code"]
                key = f"{code}|{reg}"
                default_weight = float(c.get("default_weight", 1.0))
                plan = batch["plans"][key]
                readiness = plan["readiness"]
                stats = readiness["per_factor"].get("net_return", {})
                old = plan["old_weight"]
                modes[market] = plan["mode"]
                checked += 1
                if old is None:
                    statuses[key] = "TARGET_MISSING"
                    continue
                sample_size = int(readiness["training"]["units"])
                if sample_size < min_samples:
                    skipped_low_sample += 1
                    continue

                win_rate = float(stats.get("win_rate") or 0.0)
                avg_ret = float(stats.get("mean") or 0.0)
                win_term = _clamp((win_rate - 50.0) / 50.0, -1.0, 1.0)
                ret_term = _clamp(avg_ret / 8.0, -1.0, 1.0)
                target = default_weight * (1.0 + 0.45 * win_term + 0.35 * ret_term)
                target = _clamp(target, 0.45, 1.90)
                new_weight = float(round(_clamp(old * (1.0 - alpha) + target * alpha, 0.45, 1.90), 4))
                if abs(new_weight - old) < 0.01:
                    statuses[key] = "NO_CHANGE"
                    continue
                proposals[key] = new_weight
                reasons[key] = (
                    f"auto_rebalance(win_rate={win_rate:.1f}%, avg_ret={avg_ret:.2f}%, "
                    f"samples={sample_size}, alpha={alpha:.2f})"
                )

            # One all-or-nothing write transaction per market batch.
            if not proposals:
                continue
            result = apply_calibration_batch(
                db,
                plans={key: batch["plans"][key] for key in proposals},
                proposals=proposals,
                reasons=reasons,
            )
            for key, status in result["results"].items():
                statuses[key] = status
            for key in result["applied"]:
                changed += 1
                rows_changed.append(
                    {
                        "strategy_code": key.partition("|")[0],
                        "market": market,
                        "old_weight": float(batch["plans"][key]["old_weight"]),
                        "new_weight": proposals[key],
                        "sample_size": int(
                            batch["plans"][key]["cohort"]["counts"]["units"]),
                    }
                )

        db.commit()
        first_plan = next(iter(statuses.values()), None)
        return {
            "window_days": window_days,
            "min_samples": min_samples,
            "alpha": alpha,
            "checked": checked,
            "changed": changed,
            "skipped_low_sample": skipped_low_sample,
            "changes": rows_changed,
            "statuses": statuses,
            "modes": modes,
            "mode": next(iter(modes.values()), None),
        }
    except Exception as e:
        db.rollback()
        logger.warning(f"策略調權失敗: {e}")
        return {
            "window_days": window_days,
            "min_samples": min_samples,
            "alpha": alpha,
            "checked": 0,
            "changed": 0,
            "skipped_low_sample": 0,
            "changes": [],
            "error": str(e),
        }
    finally:
        db.close()


def get_strategy_stats(*, days: int = 45) -> dict:
    ensure_strategy_catalog()
    days = max(1, min(int(days or 45), 365))
    since = utc_now() - timedelta(days=days)
    db = SessionLocal()
    try:
        latest_snapshot_row = (
            db.query(StrategySignalRun.snapshot_date)
            .order_by(StrategySignalRun.snapshot_date.desc())
            .first()
        )
        snapshot = latest_snapshot_row[0] if latest_snapshot_row else ""
        coverage = {
            "snapshot_date": snapshot,
            "total_signals": 0,
            "active_signals": 0,
            "watchlist_signals": 0,
            "market_scan_signals": 0,
            "mixed_signals": 0,
        }
        if snapshot:
            total_signals = (
                db.query(func.count(StrategySignalRun.id))
                .filter(StrategySignalRun.snapshot_date == snapshot)
                .scalar()
            ) or 0
            active_signals = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.status == "active",
                )
                .scalar()
            ) or 0
            market_scan = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.source_pool.in_(("market_scan", "mixed")),
                )
                .scalar()
            ) or 0
            mixed = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.source_pool == "mixed",
                )
                .scalar()
            ) or 0
            watchlist = max(0, int(total_signals) - int(market_scan))
            coverage = {
                "snapshot_date": snapshot,
                "total_signals": int(total_signals),
                "active_signals": int(active_signals),
                "watchlist_signals": int(watchlist),
                "market_scan_signals": int(market_scan),
                "mixed_signals": int(mixed),
                "market_scan_share_pct": round((market_scan / total_signals * 100.0), 2)
                if total_signals
                else 0.0,
            }

        outcome_rows = (
            db.query(
                StrategyOutcome.strategy_code,
                StrategyOutcome.stock_market,
                StrategyOutcome.horizon_days,
                func.count(StrategyOutcome.id).label("total"),
                func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
                func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
            )
            .filter(
                StrategyOutcome.created_at >= since,
                StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            )
            .group_by(
                StrategyOutcome.strategy_code,
                StrategyOutcome.stock_market,
                StrategyOutcome.horizon_days,
            )
            .all()
        )
        profiles = get_strategy_profile_map()
        weights = (
            db.query(StrategyWeight)
            .filter(StrategyWeight.regime == "default")
            .all()
        )
        weight_map = {
            (w.strategy_code, (w.market or "ALL").upper()): float(w.weight or 1.0)
            for w in weights
        }

        by_strategy: list[dict] = []
        for code, market, horizon, total, wins, avg_ret in outcome_rows:
            c = (code or "").strip()
            m = (market or "ALL").strip().upper()
            t = int(total or 0)
            w = int(wins or 0)
            win_rate = (w / t * 100.0) if t else 0.0
            prof = profiles.get(c) or {}
            default_weight = float(prof.get("default_weight", 1.0))
            current_weight = weight_map.get((c, m), weight_map.get((c, "ALL"), default_weight))
            by_strategy.append(
                {
                    "strategy_code": c,
                    "population": "legacy-unlabelled",
                    "return_basis": "legacy calendar-day close projection",
                    "strategy_name": prof.get("name") or c,
                    "strategy_version": prof.get("version") or "v1",
                    "market": m,
                    "risk_level": prof.get("risk_level") or "medium",
                    "risk_level_label": _risk_label(prof.get("risk_level") or "medium"),
                    "horizon_days": int(horizon or 0),
                    "sample_size": t,
                    "wins": w,
                    "win_rate": round(win_rate, 2),
                    "avg_return_pct": round(float(avg_ret or 0.0), 4),
                    "default_weight": round(default_weight, 4),
                    "current_weight": round(float(current_weight), 4),
                }
            )
        by_strategy.sort(key=lambda x: (x["sample_size"], x["win_rate"], x["avg_return_pct"]), reverse=True)

        by_market_rows = (
            db.query(
                StrategyOutcome.stock_market,
                func.count(StrategyOutcome.id).label("total"),
                func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
                func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
            )
            .filter(
                StrategyOutcome.created_at >= since,
                StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            )
            .group_by(StrategyOutcome.stock_market)
            .all()
        )
        by_market = []
        for market, total, wins, avg_ret in by_market_rows:
            t = int(total or 0)
            w = int(wins or 0)
            by_market.append(
                {
                    "market": (market or "CN").strip().upper(),
                    "population": "legacy-unlabelled",
                    "return_basis": "legacy calendar-day close projection",
                    "total": t,
                    "wins": w,
                    "win_rate": round((w / t * 100.0), 2) if t else 0.0,
                    "avg_return_pct": round(float(avg_ret or 0.0), 4),
                }
            )

        updates = (
            db.query(func.count(StrategyWeightHistory.id))
            .filter(StrategyWeightHistory.created_at >= since)
            .scalar()
        ) or 0

        top_signals = []
        regime_items: list[dict] = []
        risk_items: list[dict] = []
        factor_stats = {
            "avg_alpha_score": 0.0,
            "avg_catalyst_score": 0.0,
            "avg_quality_score": 0.0,
            "avg_risk_penalty": 0.0,
            "avg_crowd_penalty": 0.0,
            "sample_size": 0,
        }
        constrained_count = 0
        if snapshot:
            rows = (
                db.query(StrategySignalRun)
                .filter(StrategySignalRun.snapshot_date == snapshot,
                        _current_candidate_identity_filter())
                .order_by(StrategySignalRun.rank_score.desc(), StrategySignalRun.updated_at.desc())
                .limit(20)
                .all()
            )
            top_signals = [_format_signal(x) for x in rows]
            constrained_count = sum(
                1
                for x in rows
                if isinstance(x.payload, dict) and bool(x.payload.get("constrained"))
            )
            factor_rows = (
                db.query(
                    func.avg(StrategyFactorSnapshot.alpha_score).label("alpha"),
                    func.avg(StrategyFactorSnapshot.catalyst_score).label("catalyst"),
                    func.avg(StrategyFactorSnapshot.quality_score).label("quality"),
                    func.avg(StrategyFactorSnapshot.risk_penalty).label("risk"),
                    func.avg(StrategyFactorSnapshot.crowd_penalty).label("crowd"),
                    func.count(StrategyFactorSnapshot.id).label("cnt"),
                )
                .filter(StrategyFactorSnapshot.snapshot_date == snapshot)
                .first()
            )
            if factor_rows:
                factor_stats = {
                    "avg_alpha_score": round(float(factor_rows.alpha or 0.0), 4),
                    "avg_catalyst_score": round(float(factor_rows.catalyst or 0.0), 4),
                    "avg_quality_score": round(float(factor_rows.quality or 0.0), 4),
                    "avg_risk_penalty": round(float(factor_rows.risk or 0.0), 4),
                    "avg_crowd_penalty": round(float(factor_rows.crowd or 0.0), 4),
                    "sample_size": int(factor_rows.cnt or 0),
                }
            regimes = (
                db.query(MarketRegimeSnapshot)
                .filter(MarketRegimeSnapshot.snapshot_date == snapshot)
                .order_by(MarketRegimeSnapshot.market.asc())
                .all()
            )
            for r in regimes:
                regime_items.append(
                    {
                        "snapshot_date": r.snapshot_date,
                        "market": r.market,
                        "regime": r.regime,
                        "regime_label": _regime_label(r.regime or "neutral"),
                        "regime_score": round(float(r.regime_score or 0.0), 4),
                        "confidence": round(float(r.confidence or 0.0), 4),
                        "breadth_up_pct": r.breadth_up_pct,
                        "avg_change_pct": r.avg_change_pct,
                        "volatility_pct": r.volatility_pct,
                        "active_ratio": r.active_ratio,
                        "sample_size": int(r.sample_size or 0),
                        "meta": r.meta or {},
                    }
                )
            risks = (
                db.query(PortfolioRiskSnapshot)
                .filter(PortfolioRiskSnapshot.snapshot_date == snapshot)
                .order_by(PortfolioRiskSnapshot.market.asc())
                .all()
            )
            for r in risks:
                risk_items.append(
                    {
                        "snapshot_date": r.snapshot_date,
                        "market": r.market,
                        "total_signals": int(r.total_signals or 0),
                        "active_signals": int(r.active_signals or 0),
                        "held_signals": int(r.held_signals or 0),
                        "unheld_signals": int(r.unheld_signals or 0),
                        "high_risk_ratio": r.high_risk_ratio,
                        "concentration_top5": r.concentration_top5,
                        "avg_rank_score": r.avg_rank_score,
                        "risk_level": r.risk_level or "medium",
                        "meta": r.meta or {},
                    }
                )

        from src.modules.strategy.outcomes import point_in_time_outcome_stats
        return {
            "window_days": days,
            "legacy_outcome_basis": {
                "population": "legacy-unlabelled",
                "return_basis": "legacy calendar-day close projection",
            },
            "outcomes_v2": point_in_time_outcome_stats(days=days, db=db),
            "coverage": coverage,
            "constraints": {
                "constrained_top20": int(constrained_count),
            },
            "factor_stats": factor_stats,
            "regimes": regime_items,
            "portfolio_risk": risk_items,
            "by_strategy": by_strategy[:300],
            "by_market": by_market,
            "weight_updates": {
                "window_days": days,
                "changed": int(updates),
            },
            "top_signals": top_signals,
        }
    finally:
        db.close()


def list_market_regime_snapshots(
    *,
    snapshot_date: str = "",
    market: str = "",
    limit: int = 100,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(MarketRegimeSnapshot)
        snap = (snapshot_date or "").strip()
        if snap:
            q = q.filter(MarketRegimeSnapshot.snapshot_date == snap)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(MarketRegimeSnapshot.market == mkt)
        rows = (
            q.order_by(
                MarketRegimeSnapshot.snapshot_date.desc(),
                MarketRegimeSnapshot.market.asc(),
            )
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "snapshot_date": r.snapshot_date,
                    "market": r.market,
                    "regime": r.regime or "neutral",
                    "regime_label": _regime_label(r.regime or "neutral"),
                    "regime_score": round(float(r.regime_score or 0.0), 4),
                    "confidence": round(float(r.confidence or 0.0), 4),
                    "breadth_up_pct": r.breadth_up_pct,
                    "avg_change_pct": r.avg_change_pct,
                    "volatility_pct": r.volatility_pct,
                    "active_ratio": r.active_ratio,
                    "sample_size": int(r.sample_size or 0),
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()


def list_portfolio_risk_snapshots(
    *,
    snapshot_date: str = "",
    market: str = "",
    limit: int = 100,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(PortfolioRiskSnapshot)
        snap = (snapshot_date or "").strip()
        if snap:
            q = q.filter(PortfolioRiskSnapshot.snapshot_date == snap)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(PortfolioRiskSnapshot.market == mkt)
        rows = (
            q.order_by(
                PortfolioRiskSnapshot.snapshot_date.desc(),
                PortfolioRiskSnapshot.market.asc(),
            )
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "snapshot_date": r.snapshot_date,
                    "market": r.market,
                    "total_signals": int(r.total_signals or 0),
                    "active_signals": int(r.active_signals or 0),
                    "held_signals": int(r.held_signals or 0),
                    "unheld_signals": int(r.unheld_signals or 0),
                    "high_risk_ratio": r.high_risk_ratio,
                    "concentration_top5": r.concentration_top5,
                    "avg_rank_score": r.avg_rank_score,
                    "risk_level": r.risk_level or "medium",
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()


def get_strategy_factor_snapshot(signal_run_id: int) -> dict:
    db = SessionLocal()
    try:
        row = (
            db.query(StrategyFactorSnapshot)
            .filter(StrategyFactorSnapshot.signal_run_id == int(signal_run_id))
            .first()
        )
        if not row:
            return {}
        return {
            "id": row.id,
            "signal_run_id": int(row.signal_run_id),
            "snapshot_date": row.snapshot_date,
            "stock_symbol": row.stock_symbol,
            "stock_market": row.stock_market,
            "strategy_code": row.strategy_code,
            "alpha_score": float(row.alpha_score or 0.0),
            "catalyst_score": float(row.catalyst_score or 0.0),
            "quality_score": float(row.quality_score or 0.0),
            "risk_penalty": float(row.risk_penalty or 0.0),
            "crowd_penalty": float(row.crowd_penalty or 0.0),
            "source_bonus": float(row.source_bonus or 0.0),
            "regime_multiplier": float(row.regime_multiplier or 1.0),
            "final_score": float(row.final_score or 0.0),
            "factor_payload": row.factor_payload or {},
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
    finally:
        db.close()


def list_strategy_weight_history(
    *,
    strategy_code: str = "",
    market: str = "",
    limit: int = 200,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(StrategyWeightHistory)
        code = (strategy_code or "").strip()
        if code:
            q = q.filter(StrategyWeightHistory.strategy_code == code)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(StrategyWeightHistory.market == mkt)
        rows = (
            q.order_by(StrategyWeightHistory.created_at.desc())
            .limit(max(1, min(int(limit), 2000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "strategy_code": r.strategy_code,
                    "market": r.market,
                    "regime": r.regime,
                    "old_weight": float(r.old_weight or 1.0),
                    "new_weight": float(r.new_weight or 1.0),
                    "reason": r.reason or "",
                    "window_days": int(r.window_days or 0),
                    "sample_size": int(r.sample_size or 0),
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()
