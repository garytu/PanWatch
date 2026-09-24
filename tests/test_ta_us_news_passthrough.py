"""美股 get_news 路由:應透傳上游(Yahoo)而非被東財關鍵詞搜尋截走;中文行業詞才走東財。

迴歸 bug:0.3.0 新聞分析師對美股 ticker(BABA)調 get_news,原邏輯只判 `not is_panwatch_routable`,
把美股 ticker 也送進東財關鍵詞搜尋 → 搜不到 → 返回「未搜到」空結果,美股拿不到個股新聞。
修復:關鍵詞新聞分支再加「含中文」閘,純字母 ticker 落到上游透傳。
"""

from __future__ import annotations

from src.modules.automation.tradingagents import toolkit_adapter as tk


def test_looks_like_cn_keyword_distinguishes_ticker_from_cn_query():
    """純字母美股 ticker 不算中文行業詞;含中文(行業/主題)才算。"""
    assert tk._looks_like_cn_keyword("汽車行業") is True
    assert tk._looks_like_cn_keyword("新能源汽車") is True
    assert tk._looks_like_cn_keyword("BABA") is False
    assert tk._looks_like_cn_keyword("NVDA") is False
    assert tk._looks_like_cn_keyword("") is False


def test_us_ticker_get_news_passes_through_to_upstream(monkeypatch):
    """美股 get_news(BABA)→ 走上游 vendor(Yahoo),不進東財關鍵詞搜尋。"""
    calls = {"keyword": 0, "upstream": 0}

    def fake_keyword(_sym):
        calls["keyword"] += 1
        return "東財關鍵詞新聞"

    def fake_upstream(_method, *_a, **_k):
        calls["upstream"] += 1
        return "UPSTREAM YAHOO NEWS for BABA"

    monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
    monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

    out = tk._patched_route_to_vendor("get_news", "BABA", "2026-06-15", "2026-06-22")

    assert calls["upstream"] == 1
    assert calls["keyword"] == 0
    assert "UPSTREAM" in str(out)


def test_cn_keyword_get_news_goes_to_eastmoney(monkeypatch):
    """中文行業詞(汽車行業)→ 走東財關鍵詞搜尋,不透傳上游。"""
    calls = {"keyword": 0, "upstream": 0}

    def fake_keyword(_sym):
        calls["keyword"] += 1
        return "東財搜到的行業新聞"

    def fake_upstream(_method, *_a, **_k):
        calls["upstream"] += 1
        return "UPSTREAM"

    monkeypatch.setattr(tk, "_serve_keyword_news", fake_keyword)
    monkeypatch.setattr(tk, "_real_route_to_vendor", fake_upstream)

    out = tk._patched_route_to_vendor("get_news", "汽車行業", "2026-06-15", "2026-06-22")

    assert calls["keyword"] == 1
    assert calls["upstream"] == 0
    assert "行業新聞" in str(out)
