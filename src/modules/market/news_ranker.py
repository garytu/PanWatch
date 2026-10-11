from __future__ import annotations

import re
from collections import Counter
from datetime import datetime


POSITIVE_HINTS = (
    "簽約",
    "中標",
    "增長",
    "上調",
    "創新高",
    "利好",
    "增持",
    "回購",
    "扭虧",
    "超預期",
)

NEGATIVE_HINTS = (
    "下調",
    "減持",
    "虧損",
    "暴跌",
    "訴訟",
    "風險",
    "違規",
    "處罰",
    "利空",
    "退市",
)


def _to_naive_local(dt: datetime) -> datetime:
    """統一轉為本地時區的 naive datetime，便於與 datetime.now() 比較。"""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone().replace(tzinfo=None)


def parse_news_time(value: str | datetime | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_naive_local(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None

    text = str(value).strip()
    if not text:
        return None

    normalized = text.replace("T", " ").replace("Z", "+00:00")
    full_fmts = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    )
    for fmt in full_fmts:
        try:
            return _to_naive_local(datetime.strptime(normalized, fmt))
        except Exception:
            continue

    # 常見月日格式（無年份），按當前年份補齊。
    for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M", "%m/%d %H:%M:%S", "%m/%d %H:%M"):
        try:
            partial = datetime.strptime(normalized, fmt)
            now = datetime.now()
            return partial.replace(year=now.year)
        except Exception:
            continue

    try:
        return _to_naive_local(datetime.fromisoformat(normalized))
    except Exception:
        return None


def dedupe_news_items(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict] = []
    for it in items:
        source = str(it.get("source") or "")
        external_id = str(it.get("external_id") or "")
        title = str(it.get("title") or "")
        key = (source, external_id, title)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _sentiment_from_text(text: str) -> str:
    pos = sum(1 for k in POSITIVE_HINTS if k in text)
    neg = sum(1 for k in NEGATIVE_HINTS if k in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def rank_news_items(items: list[dict], symbol: str = "") -> list[dict]:
    def score(it: dict) -> tuple[float, float]:
        title = str(it.get("title") or "")
        content = str(it.get("content") or "")
        text = f"{title} {content}"
        importance = float(it.get("importance") or 0)
        s = importance * 5.0

        if symbol and symbol in str(it.get("symbols") or []):
            s += 2.0
        if any(k in title for k in ("重大", "業績", "增持", "減持", "停牌", "解禁", "回購", "分紅", "快報")):
            s += 2.0
        if "公告" in title:
            s += 1.0

        ts = parse_news_time(str(it.get("time") or "")) or datetime.min
        s2 = ts.timestamp() if ts != datetime.min else 0
        return s, s2

    return sorted(items, key=score, reverse=True)


def summarize_news_topics(items: list[dict], max_topics: int = 6) -> dict:
    if not items:
        return {
            "summary": "近期無顯著新聞主題",
            "topics": [],
            "sentiment": "neutral",
            "counts": {"positive": 0, "negative": 0, "neutral": 0},
        }

    word_counter: Counter[str] = Counter()
    senti_counter: Counter[str] = Counter()

    for it in items:
        title = str(it.get("title") or "")
        content = str(it.get("content") or "")
        text = f"{title} {content}".strip()
        sentiment = _sentiment_from_text(text)
        senti_counter[sentiment] += 1

        words = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", title)
        for w in words:
            if w in ("公司", "公告", "今日", "訊息", "顯示", "釋出", "表示", "相關"):
                continue
            word_counter[w] += 1

    topics = [w for w, _ in word_counter.most_common(max_topics)]
    if senti_counter["positive"] > senti_counter["negative"]:
        senti = "positive"
    elif senti_counter["negative"] > senti_counter["positive"]:
        senti = "negative"
    else:
        senti = "neutral"

    if topics:
        summary = f"主題集中在：{'、'.join(topics[: max_topics])}；整體情緒{('偏多' if senti == 'positive' else '偏空' if senti == 'negative' else '中性')}"
    else:
        summary = f"可用新聞較少，整體情緒{('偏多' if senti == 'positive' else '偏空' if senti == 'negative' else '中性')}"

    return {
        "summary": summary,
        "topics": topics,
        "sentiment": senti,
        "counts": {
            "positive": int(senti_counter["positive"]),
            "negative": int(senti_counter["negative"]),
            "neutral": int(senti_counter["neutral"]),
        },
    }
