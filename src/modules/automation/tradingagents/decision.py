"""TradingAgents 輸出 → PanWatch AnalysisResult 對映。

TradingAgents 的 `final_state` 是 LangGraph 累積的 dict,關鍵欄位(摘自上游):
- market_report / social_report / news_report / fundamentals_report: 4 個分析師報告
- investment_debate_state: 看多看空辯論歷史 {history, current_response, judge_decision}
- trader_investment_plan: 交易員意見
- risk_judge_decision: 風控判定
- final_trade_decision: PM 整合後的最終決策書
- (processed_signal): "BUY" / "HOLD" / "SELL"

分析結果落庫後，檔案下半部負責可選的模擬交易訊號橋接；兩者共享同一套 REVIEW 安全對映。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from src.modules.automation.base import AnalysisResult

__all__ = [
    "DECISION_LABEL_MAP",
    "map_state_to_result",
    "maybe_emit_paper_trading_signal",
]


# 上游 5 檔評級 → PanWatch 顯示標籤
RATING_LABEL_MAP = {
    "buy": "買入",
    "overweight": "增持",
    "hold": "持有",
    "underweight": "減持",
    "sell": "賣出",
}

# 5 檔 → 3 檔(給 action 欄位;前端 'buy' | 'hold' | 'sell')
RATING_ACTION_MAP = {
    "buy": "buy",
    "overweight": "buy",
    "hold": "hold",
    "underweight": "sell",
    "sell": "sell",
}

# 0.4.0 起,上游無法解析 PM 評級時返回 REVIEW。它不是可交易的 Hold:
# 在不擴充套件前端 3 檔 action API 的前提下,以 hold 阻止自動交易,並保留原始狀態供展示/提醒。
REVIEW_RATING = "review"
REVIEW_LABEL = "待人工複核"

# 舊欄位名相容:某些下游程式碼可能 import DECISION_LABEL_MAP
DECISION_LABEL_MAP = RATING_LABEL_MAP


def map_state_to_result(
    *,
    stock: Any,
    ta_result: dict[str, Any],
    model_label: str = "",
) -> AnalysisResult:
    """主入口:把 TradingAgents 的 final_state 對映成 AnalysisResult。

    Args:
        stock: PanWatch StockConfig(symbol/name/market)
        ta_result: {"decision": str, "final_state": dict, "cost_usd": float}
        model_label: 形如 "deepseek/deepseek-chat",寫到 markdown 末尾
    """
    state = ta_result.get("final_state") or {}
    cost_usd = float(ta_result.get("cost_usd", 0.0) or 0.0)

    # 評級以 PM 正文(final_trade_decision,使用者實際看到的最終決策書)為權威來源:
    # 上游 propagate() 第二個返回的 decision 是對正文的二次提煉,會失真(正文寫"賣出"
    # 卻返回 "HOLD"),所以優先解析正文裡的顯式評級標籤。
    # 優先順序:正文顯式標籤 > 上游 decision > 正文模糊掃描兜底。
    final_text = state.get("final_trade_decision") or ""
    upstream_rating = (ta_result.get("decision") or "").strip().lower()
    if upstream_rating == REVIEW_RATING:
        # REVIEW 是上游對整份 PM 輸出的不可解析判定,不能被正文裡的評級詞覆蓋。
        rating_raw = REVIEW_RATING
    else:
        rating_raw = _parse_rating_label(final_text)
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = upstream_rating
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = _parse_rating_from_text(final_text)

    review_required = rating_raw == REVIEW_RATING
    action = RATING_ACTION_MAP.get(rating_raw, "hold")
    action_label = REVIEW_LABEL if review_required else RATING_LABEL_MAP.get(rating_raw, "持有")

    confidence = _extract_confidence(state, rating_raw)
    short_reason = _short_reason(state)

    suggestion = {
        "action": action,
        "action_label": action_label,
        "rating_raw": rating_raw or "hold",  # 保留原始 5 檔,前端/歷史可查
        "review_required": review_required,
        "upstream_decision": upstream_rating,
        "signal": _truncate(state.get("trader_investment_plan", ""), 200),
        "reason": state.get("final_trade_decision") or short_reason,
        "should_alert": review_required or rating_raw in ("buy", "overweight", "underweight", "sell"),
        "agent_name": "tradingagents",
        "agent_label": "TradingAgents 深度",
        "confidence": confidence,
    }

    content = _render_markdown(state, suggestion, model_label, cost_usd)
    # 詳細資訊頁可點選連結(配了 panwatch_base_url 才出現)
    from datetime import date as _date
    from src.modules.research.analysis_link import analysis_detail_markdown
    _link = analysis_detail_markdown(stock.symbol, _date.today().isoformat())
    if _link:
        content = content.rstrip() + f"\n\n---\n{_link}"
    # 通知體只放「最終決策」(決策摘要 + PM 決策書) + 詳細資訊連結;
    # 交易員/研究主管/風控辯論/四分析師等完整內容都在詳細資訊頁,避免通知過長被截斷。
    notify_content = _render_notify(state, suggestion, cost_usd, _link)

    return AnalysisResult(
        agent_name="tradingagents",
        title=f"【深度】{stock.name}({stock.symbol}):{suggestion['action_label']}",
        content=content,
        notify_content=notify_content,
        raw_data={
            "suggestion": suggestion,
            "cost_usd": cost_usd,
            "should_alert": suggestion["should_alert"],
            "decision": action,           # 相容舊欄位(3 檔)
            "rating": rating_raw or "hold",  # 新欄位(5 檔原始)
            "upstream_decision": upstream_rating,
            "confidence": confidence,
            "debate_history": _extract_debate(state),
            "risk_judgment": _risk_judgment(state),
            "risk_debate": _extract_risk_debate(state),
            "analyst_reports": {
                "market": state.get("market_report") or "",
                # 上游情緒分析師欄位是 sentiment_report;相容舊 social_report
                "social": state.get("sentiment_report") or state.get("social_report") or "",
                "news": state.get("news_report") or "",
                "fundamentals": state.get("fundamentals_report") or "",
            },
            "final_decision": state.get("final_trade_decision") or "",
            "trader_plan": state.get("trader_investment_plan") or "",
        },
    )


# ---- helpers ----


# 分隔符字元類同時覆蓋半形(: -)與全形(： －)標點 —— 真實中文 PM 正文用全形冒號"：",
# 早期只認半形":"導致"最終交易決策：Buy"匹配不到、回退到上游失真的 decision。
_RATING_TEXT_RE = re.compile(
    r"(?:Rating|評級|最終交易決策|Final\s+(?:Trade\s+)?Decision|FINAL\s+TRANSACTION\s+PROPOSAL)"
    r"[\s\*:：\-—－]+(\*\*)?\s*(Buy|Overweight|Hold|Underweight|Sell|買入|增持|持有|減持|賣出)",
    re.I,
)
_RATING_ZH_TO_EN = {
    "買入": "buy", "增持": "overweight", "持有": "hold",
    "減持": "underweight", "賣出": "sell",
}


def _parse_rating_label(text: str) -> str:
    """只解析 PM 正文裡的**顯式評級標籤**(最終交易決策/評級/FINAL TRANSACTION PROPOSAL: X)。

    不做模糊關鍵詞掃描 —— 避免正文裡"否決了之前的買入建議"這類幹擾詞被誤判。
    用作評級提取的首選,確保展示與使用者可見的最終決策書一致。
    """
    if not text:
        return ""
    m = _RATING_TEXT_RE.search(text)
    if m:
        word = m.group(2).lower()
        if word in _RATING_ZH_TO_EN:
            word = _RATING_ZH_TO_EN[word]
        if word in RATING_LABEL_MAP:
            return word
    return ""


def _parse_rating_from_text(text: str) -> str:
    """從文本里抽 5 檔評級。優先 'Rating: X' 標籤,然後第一個 5 檔詞。"""
    if not text:
        return ""
    label = _parse_rating_label(text)
    if label:
        return label
    # 兜底:掃描整段文本里第一個出現的 5 檔英文/中文詞
    text_low = text.lower()
    for word in ("overweight", "underweight", "buy", "sell", "hold"):
        if word in text_low:
            return word
    for zh, en in _RATING_ZH_TO_EN.items():
        if zh in text:
            return en
    return ""


# 置信度正則:冒號同時認半形(:)與全形(：)——PM 中文輸出常用全形,
# 早先只認半形導致永遠抓不到、一律回退預設值。覆蓋 "置信度: 8"、"置信度：8/10"、"信心 7"。
_CONFIDENCE_PATTERNS = [
    re.compile(r"confidence[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"置信度[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"信心(?:度)?[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
]

# 抓不到顯式數字時按 5 檔評級推導基礎置信度(B 方案),而非死的 5.0:
# 強方向(買入/賣出)信心更高,中性(持有)居中。
_RATING_CONFIDENCE_FALLBACK = {
    "buy": 7.0,
    "sell": 7.0,
    "overweight": 6.0,
    "underweight": 6.0,
    "hold": 5.0,
}


def _extract_confidence(state: dict, rating_raw: str = "") -> float:
    """置信度(0-10):優先抓 PM/風控/交易員文本里的顯式數字(A 方案);
    抓不到則按評級推導(B 方案),不再一律返回 5.0。"""
    candidates = [
        state.get("final_trade_decision", ""),
        _risk_judgment(state),
        state.get("trader_investment_plan", ""),
    ]
    for text in candidates:
        if not text:
            continue
        for pat in _CONFIDENCE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 10:  # 百分制轉 0-10
                        v = v / 10
                    return max(0.0, min(10.0, v))
                except (ValueError, IndexError):
                    continue
    # B 兜底:按評級推導(無可識別評級才回退 5.0)
    return _RATING_CONFIDENCE_FALLBACK.get(rating_raw, 5.0)


def _short_reason(state: dict, limit: int = 120) -> str:
    """取一段精煉理由,優先 final_trade_decision 前 120 字。"""
    candidates = [
        state.get("final_trade_decision") or "",
        state.get("trader_investment_plan") or "",
        _risk_judgment(state),
    ]
    for text in candidates:
        text = text.strip()
        if text:
            return _truncate(text, limit)
    return ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_debate(state: dict) -> dict:
    """提取辯論歷史。上游 investment_debate_state 大致結構:
    {
        "history": "...",      # 全量辯論文本
        "current_response": ...,
        "judge_decision": ...,
    }
    """
    debate = state.get("investment_debate_state") or {}
    if not isinstance(debate, dict):
        return {}
    return {
        "history": debate.get("history", ""),
        "current_response": debate.get("current_response", ""),
        "judge_decision": debate.get("judge_decision", ""),
    }


def _risk_judgment(state: dict) -> str:
    """風控團隊裁決:上游在 risk_debate_state.judge_decision(激進/中立/保守辯論後的結論);
    上游根本沒有頂層 risk_judge_decision 欄位,早先讀它導致風控裁決一直空白。"""
    rds = state.get("risk_debate_state")
    if isinstance(rds, dict):
        jd = (rds.get("judge_decision") or "").strip()
        if jd:
            return jd
    return (state.get("risk_judge_decision") or "").strip()  # 相容兜底


def _extract_risk_debate(state: dict) -> dict:
    """風控團隊辯論(激進/中立/保守 + 裁決),結構對稱 _extract_debate。
    上游 risk_debate_state.history 是三方交替的完整辯論文本。"""
    rds = state.get("risk_debate_state")
    if not isinstance(rds, dict):
        return {}
    return {
        "history": rds.get("history", ""),
        "judge_decision": rds.get("judge_decision", ""),
    }


def _render_notify(
    state: dict, suggestion: dict, cost_usd: float, link_md: str = ""
) -> str:
    """通知體:只展示「最終決策」(決策摘要 + PM 最終決策書) + 詳細資訊連結。

    交易員執行計劃 / 研究主管裁決 / 風控辯論 / 四位分析師報告等完整內容都在詳細資訊頁,
    不進通知 —— 既符合"通知只看最終決策"的訴求,也避免推送過長被各管道截斷。
    """
    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(評級:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts = [
        f"## 最終決策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    ]
    final_text = (state.get("final_trade_decision") or "").strip()
    if final_text:
        parts.append(final_text + "\n")
    parts.append(
        f"\n_成本 ${cost_usd:.4f} · 交易員 / 研究主管 / 風控辯論 / 四分析師完整內容見詳細資訊_"
    )
    if link_md:
        parts.append(f"\n\n{link_md}")
    return "\n".join(parts)


def _render_markdown(
    state: dict, suggestion: dict, model_label: str, cost_usd: float
) -> str:
    parts = []

    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(評級:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts.append(
        f"## 最終決策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    )

    # 9 個 Agent 鏈路:PM(決策書) → Trader → 研究主管 → 風控 → 4 位分析師摘要
    if state.get("final_trade_decision"):
        parts.append(f"### 🎯 PM 最終決策書\n\n{state['final_trade_decision']}\n")

    if state.get("trader_investment_plan"):
        parts.append(f"### 💼 交易員執行計劃\n\n{state['trader_investment_plan']}\n")

    # 研究主管裁決 — 看多/看空辯論後的結論,之前只在摺疊的辯論 section 末尾
    debate = state.get("investment_debate_state") or {}
    judge_decision = ""
    if isinstance(debate, dict):
        judge_decision = (debate.get("judge_decision") or "").strip()
    if judge_decision:
        parts.append(f"### ⚖️ 研究主管裁決(看多 vs 看空)\n\n{judge_decision}\n")

    risk_jd = _risk_judgment(state)
    if risk_jd:
        parts.append(f"### 🛡️ 風控辯論裁決\n\n{risk_jd}\n")

    # 4 位分析師完整報告不再塞進主體 markdown(早先截 300 字會把財務表格截在表頭)。
    # 完整內容在 raw_data.analyst_reports,由前端 tab 完整渲染(含 GFM 表格)。

    parts.append(
        "\n---\n"
        f"_本分析由 TradingAgents 9-Agent 框架生成(技術/情緒/新聞/基本面 → 看多看空辯論 "
        f"→ 研究主管 → 交易員 → 風控辯論 → PM)。僅供學習研究參考,不構成投資建議。_\n"
        f"\n成本:${cost_usd:.4f}"
    )
    if model_label:
        parts.append(f" · AI:{model_label}")

    return "\n".join(parts)


# ============================================================================
# Paper trading bridge
# ============================================================================

logger = logging.getLogger(__name__)


def maybe_emit_paper_trading_signal(
    *,
    stock_symbol: str,
    stock_market: str,
    stock_name: str,
    decision: str,
    confidence: float,
    signal_text: str,
    reason: str,
    current_price: float | None,
    enabled: bool,
) -> bool:
    """將 TA 決策寫入 StrategySignalRun。返回是否實際寫入。

    - 僅 enabled=True 且 decision in (buy, add) 時寫入(SELL 不開新倉)
    - entry_low/high 用當前價 ±2% 作為入場區間
    - stop_loss 用入場價 -5%,target_price +10%(粗粒度,可以 Phase C 讓 TA 輸出更精確)
    - 同標的同日去重:strategy_code+source_candidate_id 唯一性
    """
    if not enabled:
        return False
    action = (decision or "").lower()
    if action not in ("buy", "add"):
        return False
    if not current_price or current_price <= 0:
        logger.warning(
            f"[TA paper] {stock_symbol} 當前價缺失,跳過寫訊號"
        )
        return False

    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import StrategySignalRun

    snapshot_date = date.today().isoformat()
    entry_low = round(current_price * 0.98, 2)
    entry_high = round(current_price * 1.02, 2)
    stop_loss = round(current_price * 0.95, 3)
    target_price = round(current_price * 1.10, 3)

    db = SessionLocal()
    try:
        # 同標的當日重複觸發 → upsert(source_candidate_id 是 Integer,用 0 當 TA 專用 sentinel)
        source_id = 0
        existing = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date == snapshot_date,
                StrategySignalRun.stock_symbol == stock_symbol,
                StrategySignalRun.stock_market == stock_market,
                StrategySignalRun.strategy_code == "tradingagents",
                StrategySignalRun.source_candidate_id == source_id,
            )
            .first()
        )
        if existing:
            existing.action = action
            existing.action_label = DECISION_LABEL_MAP.get(action, "買入")
            existing.signal = signal_text[:500]
            existing.reason = reason[:1000]
            existing.confidence = confidence
            existing.entry_low = entry_low
            existing.entry_high = entry_high
            existing.stop_loss = stop_loss
            existing.target_price = target_price
            existing.status = "active"
        else:
            row = StrategySignalRun(
                snapshot_date=snapshot_date,
                stock_symbol=stock_symbol,
                stock_market=stock_market,
                stock_name=stock_name or stock_symbol,
                strategy_code="tradingagents",
                strategy_name="TradingAgents 深度分析",
                strategy_version="v1",
                risk_level="medium",
                source_pool="watchlist",
                score=float(confidence or 5.0),
                rank_score=float(confidence or 5.0) * 10,  # 給個偏向中等的分數
                confidence=float(confidence or 5.0) / 10,
                status="active",
                action=action,
                action_label=DECISION_LABEL_MAP.get(action, "買入"),
                signal=signal_text[:500],
                reason=reason[:1000],
                evidence=[],
                holding_days=10,  # TA 給的 time_horizon 是中長期
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_price=target_price,
                invalidation="價格跌破停損位 / 基本面惡化",
                plan_quality=70,
                source_agent="tradingagents",
                source_candidate_id=source_id,
            )
            db.add(row)
        db.commit()
        logger.info(
            f"[TA paper] 已寫訊號: {stock_symbol} {action} "
            f"entry=[{entry_low}, {entry_high}] stop={stop_loss} target={target_price}"
        )
        return True
    except Exception as e:
        logger.warning(f"[TA paper] 寫 StrategySignalRun 失敗: {e}")
        db.rollback()
        return False
    finally:
        db.close()
