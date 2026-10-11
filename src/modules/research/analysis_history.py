"""分析歷史記錄管理"""
import logging
import re
from datetime import date, datetime, timedelta

from src.modules.automation.agent_catalog import infer_agent_kind
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AnalysisHistory
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)

# TradingAgents 深度分析在 AnalysisHistory 裡的 agent_name(見 agent.py: name = "tradingagents")
TA_AGENT_NAME = "tradingagents"


def save_analysis(
    agent_name: str,
    stock_symbol: str,
    content: str,
    title: str = "",
    raw_data: dict | None = None,
    analysis_date: date | None = None,
) -> bool:
    """
    儲存分析結果

    - 同一天可以覆蓋
    - 歷史記錄不可覆蓋（透過資料庫約束保證）

    Args:
        agent_name: Agent 名稱，如 "daily_report"
        stock_symbol: 股票程式碼，"*" 表示全域性分析
        content: AI 分析內容
        title: 分析標題
        raw_data: 原始資料快照
        analysis_date: 分析日期，預設今天

    Returns:
        是否儲存成功
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        payload = to_jsonable(raw_data or {})
        agent_kind = infer_agent_kind(agent_name)

        # 查詢是否已存在
        existing = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()

        if existing:
            # 更新（同一天可覆蓋）
            existing.title = title
            existing.content = content
            existing.raw_data = payload
            existing.agent_kind_snapshot = agent_kind
            logger.info(f"更新分析記錄: {agent_name}/{stock_symbol}/{date_str}")
        else:
            # 新增
            record = AnalysisHistory(
                agent_name=agent_name,
                stock_symbol=stock_symbol,
                analysis_date=date_str,
                title=title,
                content=content,
                raw_data=payload,
                agent_kind_snapshot=agent_kind,
            )
            db.add(record)
            logger.info(f"新增分析記錄: {agent_name}/{stock_symbol}/{date_str}")

        db.commit()
        return True

    except Exception as e:
        logger.error(f"儲存分析記錄失敗: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def get_analysis(
    agent_name: str,
    stock_symbol: str,
    analysis_date: date | None = None,
) -> AnalysisHistory | None:
    """
    獲取分析結果

    Args:
        agent_name: Agent 名稱
        stock_symbol: 股票程式碼
        analysis_date: 分析日期，預設今天

    Returns:
        分析記錄，或 None
    """
    if analysis_date is None:
        analysis_date = date.today()

    date_str = analysis_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date == date_str,
        ).first()
    finally:
        db.close()


def get_latest_analysis(
    agent_name: str,
    stock_symbol: str,
    before_date: date | None = None,
) -> AnalysisHistory | None:
    """
    獲取最近的分析結果（用於獲取昨日/歷史分析）

    Args:
        agent_name: Agent 名稱
        stock_symbol: 股票程式碼
        before_date: 在此日期之前的最近記錄，預設今天

    Returns:
        分析記錄，或 None
    """
    if before_date is None:
        before_date = date.today()

    date_str = before_date.strftime("%Y-%m-%d")

    db = SessionLocal()
    try:
        return db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
            AnalysisHistory.stock_symbol == stock_symbol,
            AnalysisHistory.analysis_date < date_str,
        ).order_by(AnalysisHistory.analysis_date.desc()).first()
    finally:
        db.close()


def get_analysis_history(
    agent_name: str,
    stock_symbol: str | None = None,
    limit: int = 30,
) -> list[AnalysisHistory]:
    """
    獲取分析歷史列表

    Args:
        agent_name: Agent 名稱
        stock_symbol: 股票程式碼，None 表示所有
        limit: 返回數量限制

    Returns:
        分析記錄列表，按日期倒序
    """
    db = SessionLocal()
    try:
        query = db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == agent_name,
        )

        if stock_symbol:
            query = query.filter(AnalysisHistory.stock_symbol == stock_symbol)

        return query.order_by(AnalysisHistory.analysis_date.desc()).limit(limit).all()
    finally:
        db.close()


def get_latest_ta_verdict_row(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> AnalysisHistory | None:
    """獲取某標的最近一次 TradingAgents 深度分析記錄(含當日)。

    get_latest_analysis 用 ``analysis_date < before_date`` 語義會排除當天,
    這裡傳 ``before_date = today + 1 天`` 把當天也納入。

    Args:
        symbol: 股票程式碼
        within_days: 僅在此天數內有效(超出視為過期,由呼叫方判定)
        today: 測試可注入,預設 date.today()

    Returns:
        最近的 AnalysisHistory 行,或 None。
    """
    if today is None:
        today = date.today()
    # +1 天以包含今天(get_latest_analysis 是嚴格小於)
    return get_latest_analysis(
        TA_AGENT_NAME, symbol, before_date=today + timedelta(days=1)
    )


def _clean_one_liner(text: str, max_chars: int = 120) -> str:
    """從結論正文裡清洗出一句話摘要並截斷到 ~max_chars。

    - 去掉 Markdown 標記 / 多餘空白 / 控制字元
    - 取首段(到第一個句號/換行)
    - 超長截斷並補省略號
    """
    if not text:
        return ""
    s = str(text)
    # 去 markdown 強調符、標題井號、連結殘留
    s = re.sub(r"[#*`>\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    # 取首句(中英文句號 / 換行)
    m = re.split(r"[。\.!！\n]", s, maxsplit=1)
    head = (m[0] or s).strip()
    candidate = head if len(head) >= 8 else s
    if len(candidate) > max_chars:
        candidate = candidate[:max_chars].rstrip() + "…"
    return candidate


def get_latest_ta_verdict(
    symbol: str,
    within_days: int = 14,
    today: date | None = None,
) -> dict | None:
    """抽取某標的最近一次 TA 深度結論的緊湊版本(供盤前/盤後做高權重先驗)。

    只返回 ``{rating, action_label, one_liner, date, age_days}`` —— 絕不返回全文,
    控制 token 預算。任何缺資料 / 解析異常 → None(fail-soft,不拋)。

    Args:
        symbol: 股票程式碼
        within_days: 僅採納此天數內(含當天)的記錄,過期返回 None
        today: 測試注入用,預設今天
    """
    if today is None:
        today = date.today()
    try:
        row = get_latest_ta_verdict_row(symbol, within_days=within_days, today=today)
        if row is None:
            return None

        date_str = str(getattr(row, "analysis_date", "") or "")
        try:
            row_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

        age_days = (today - row_date).days
        if age_days < 0 or age_days > max(1, int(within_days)):
            return None

        raw = getattr(row, "raw_data", None) or {}
        if not isinstance(raw, dict):
            return None
        sug = raw.get("suggestion") or {}
        if not isinstance(sug, dict):
            sug = {}

        rating = sug.get("rating_raw") or raw.get("rating") or sug.get("action") or "hold"
        action_label = sug.get("action_label") or ""
        reason = sug.get("reason") or getattr(row, "content", "") or ""
        one_liner = _clean_one_liner(reason)

        return {
            "rating": str(rating),
            "action_label": str(action_label),
            "one_liner": one_liner,
            "date": date_str,
            "age_days": int(age_days),
        }
    except Exception as e:  # 任何意外都 fail-soft
        logger.debug(f"提取 TA 深度結論失敗: {symbol} - {e}")
        return None
