"""A 股(茅臺 600519)所有資料通路的端到端單測。

覆蓋 TradingAgents 上游工具呼叫的所有路徑,確保 PanWatch 資料真的塞進了上下文:
- get_stock_data        → K 線 CSV
- get_indicators        → 單指標精煉輸出(不再返回 5008 字 K 線 8 次重複)
- get_news / get_global_news → 公告/事件列表(或 fallback 明確禁止全球新聞)
- get_fundamentals      → 真實財務摘要(akshare 資料)
- get_balance_sheet     → 真實資產負債資料
- get_cashflow          → 真實經營現金流
- get_income_statement  → 真實利潤表資料

每條都驗證:
1. 返回內容含正確的公司名 / ticker(茅臺 600519)
2. 返回內容含真實業務資料(K線日期 / 指標值 / 財務數字 / 公告標題)
3. fallback 文本明確告訴 LLM 不要瞎編
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.modules.automation.tradingagents.toolkit_adapter import (
    _serve_from_panwatch,
    panwatch_data_context,
)


class _StockMt:
    """茅臺 mock"""
    name = "貴州茅臺"
    symbol = "600519"
    market = type("M", (), {"value": "CN"})()


def _quote_mt():
    return {
        "name": "貴州茅臺",
        "current_price": 1480.50,
        "change_pct": 1.20,
        "open_price": 1465.00,
        "high_price": 1485.00,
        "low_price": 1460.00,
        "prev_close": 1463.00,
        "volume": 1_500_000,
        "turnover": 2_220_750_000,
        "pe_ratio": 24.5,
        "total_market_value": 1_860_000_000_000,
        "circulating_market_value": 1_860_000_000_000,
        "turnover_rate": 0.12,
        "industry": "白酒",
    }


def _klines_mt():
    return [
        type("K", (), {"date": "2026-05-15", "open": 1460, "high": 1485, "low": 1455, "close": 1480.50, "volume": 1_500_000})(),
        type("K", (), {"date": "2026-05-14", "open": 1450, "high": 1470, "low": 1445, "close": 1463.00, "volume": 1_200_000})(),
    ]


def _events_mt():
    e1 = MagicMock()
    e1.title = "貴州茅臺:2026年第一季度營業收入同比增長 12%"
    e1.publish_time = "2026-04-28"
    e2 = MagicMock()
    e2.title = "貴州茅臺:關於召開2026年第一次臨時股東大會的通知"
    e2.publish_time = "2026-04-15"
    return [e1, e2]


def _financial_mt():
    """模擬 akshare stock_financial_abstract 的輸出結構"""
    return {
        "periods": ["20260331", "20251231", "20250930"],
        "indicators": {
            "營業總收入": {"20260331": 50_000_000_000.0, "20251231": 180_000_000_000.0, "20250930": 130_000_000_000.0},
            "歸母淨利潤": {"20260331": 25_000_000_000.0, "20251231": 90_000_000_000.0, "20250930": 65_000_000_000.0},
            "淨利潤": {"20260331": 26_000_000_000.0, "20251231": 92_000_000_000.0, "20250930": 66_000_000_000.0},
            "扣非淨利潤": {"20260331": 24_800_000_000.0, "20251231": 89_500_000_000.0, "20250930": 64_500_000_000.0},
            "營業成本": {"20260331": 4_500_000_000.0, "20251231": 16_000_000_000.0, "20250930": 11_500_000_000.0},
            "毛利率": {"20260331": 91.0, "20251231": 91.5, "20250930": 91.2},
            "淨資產報酬率(ROE)": {"20260331": 8.5, "20251231": 32.0, "20250930": 23.0},
            "資產負債率": {"20260331": 18.0, "20251231": 17.5, "20250930": 17.8},
            "經營現金流量淨額": {"20260331": 22_000_000_000.0, "20251231": 80_000_000_000.0, "20250930": 55_000_000_000.0},
            "每股現金流": {"20260331": 17.50, "20251231": 63.60, "20250930": 43.70},
            "基本每股收益": {"20260331": 19.90, "20251231": 71.50, "20250930": 51.70},
            "股東權益合計(淨資產)": {"20260331": 290_000_000_000.0, "20251231": 280_000_000_000.0, "20250930": 270_000_000_000.0},
            "每股淨資產": {"20260331": 230.50, "20251231": 222.80, "20250930": 215.00},
            "商譽": {"20260331": 0.0, "20251231": 0.0, "20250930": 0.0},
            "銷售淨利率": {"20260331": 52.0, "20251231": 51.1, "20250930": 50.8},
            "期間費用率": {"20260331": 9.5, "20251231": 9.8, "20250930": 9.7},
            "總資產報酬率(ROA)": {"20260331": 7.0, "20251231": 26.5, "20250930": 19.0},
        },
        "categories": {},
    }


def _technical_mt():
    t = MagicMock()
    t.ma5 = 1475.20
    t.ma10 = 1468.00
    t.ma20 = 1455.50
    t.ma60 = 1420.00
    t.macd_dif = 8.50
    t.macd_dea = 6.20
    t.macd_hist = 2.30
    t.macd_cross = "金叉"
    t.rsi6 = 65.0
    t.rsi12 = 60.0
    t.rsi24 = 55.0
    t.rsi_status = "偏強"
    t.kdj_k = 75.0
    t.kdj_d = 70.0
    t.kdj_j = 85.0
    t.kdj_status = "強勢"
    t.boll_upper = 1500.0
    t.boll_mid = 1460.0
    t.boll_lower = 1420.0
    t.boll_status = "中軌上方"
    t.volume_ratio = 1.2
    t.volume_trend = "溫和放量"
    t.trend = "多頭排列"
    return t


def _full_ctx(extras=None):
    ctx = {
        "stock": _StockMt(),
        "quote": _quote_mt(),
        "klines": _klines_mt(),
        "events": _events_mt(),
        "financial": _financial_mt(),
        "technical": _technical_mt(),
        "capital_flow": [],
    }
    if extras:
        ctx.update(extras)
    return ctx


# ============================================================
# 1. get_stock_data → K 線 CSV
# ============================================================

def test_get_stock_data_returns_kline_csv_for_maotai():
    """get_stock_data 工具:返回茅臺 K 線 CSV(含日期/收盤價)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_stock_data", "600519", {})
    assert "600519" in result
    assert "貴州茅臺" in result
    assert "2026-05-15" in result
    assert "1480.5" in result  # 收盤價


# ============================================================
# 2. get_indicators → 單指標精煉(不重複 K 線 CSV)
# ============================================================

def test_get_indicators_macd_returns_macd_values_only():
    """get_indicators(symbol, 'macd', ...) 只返回 MACD 數值,不返回 K 線 CSV"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "macd", "2026-05-17", 30),
        )
    assert "MACD" in result
    assert "8.5" in result  # DIF
    assert "金叉" in result
    # 不應該是完整 K 線 CSV(那是 5008 字)
    assert len(result) < 1000


def test_get_indicators_rsi_returns_rsi_values():
    """get_indicators(symbol, 'rsi', ...) 返回 RSI 6/12/24 + 狀態"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "rsi", "2026-05-17", 30),
        )
    assert "RSI" in result
    assert "65" in result  # RSI(6)
    assert "偏強" in result


def test_get_indicators_kdj_returns_kdj_values():
    """get_indicators(symbol, 'kdj', ...) 返回 K/D/J 值"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "kdj", "2026-05-17", 30),
        )
    assert "KDJ" in result
    assert "75" in result and "70" in result and "85" in result


def test_get_indicators_boll_returns_band_values():
    """get_indicators(symbol, 'boll', ...) 返回布林帶上/中/下軌"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "boll", "2026-05-17", 30),
        )
    assert "1500" in result  # upper
    assert "1460" in result  # mid
    assert "1420" in result  # lower


def test_get_indicators_no_repeat_full_csv():
    """關鍵:即使被調 8 次不同 indicator,內容也是 8 份精煉報告而非 8 份相同 K 線 CSV"""
    with panwatch_data_context(_full_ctx()):
        macd = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "macd"))
        rsi = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "rsi"))
        boll = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "boll"))
    # 三次返回應該差異顯著
    assert macd != rsi != boll
    # 每個都應小於 1k 字元(K 線 CSV 是 5k+)
    assert max(len(macd), len(rsi), len(boll)) < 1000


# ============================================================
# 3. get_news / get_global_news → 公告事件
# ============================================================

def test_get_news_returns_company_announcements():
    """get_news 返回茅臺真實公告標題"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_news", "600519", {})
    assert "貴州茅臺" in result
    assert "營業收入同比增長" in result or "股東大會" in result


def test_get_global_news_with_empty_events_blocks_unrelated_news():
    """get_global_news 在沒事件時返回 fallback,明確禁止 LLM 拉無關全球新聞"""
    with panwatch_data_context(_full_ctx({"events": []})):
        result = _serve_from_panwatch("get_global_news", "600519", {})
    assert "DO NOT pull unrelated global news" in result
    assert "600519" in result


# ============================================================
# 4. get_fundamentals → 真實財務摘要
# ============================================================

def test_get_fundamentals_returns_real_financial_numbers():
    """get_fundamentals 返回真實營收/淨利潤/ROE(而非空 fallback)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_fundamentals", "600519", {})
    assert "600519" in result
    assert "貴州茅臺" in result
    # 真實財務資料
    assert "Real Financial Data" in result
    # 營業總收入 5000 億
    assert "500.00 億" in result or "1800.00 億" in result
    # ROE
    assert "8.50%" in result or "32.00%" in result
    # 毛利率 91%
    assert "91.00%" in result or "91.50%" in result


def test_get_fundamentals_fallback_when_no_financial():
    """沒 financial 資料時降級到 quote 輕量基本面(不能是空文本)"""
    with panwatch_data_context(_full_ctx({"financial": None})):
        result = _serve_from_panwatch("get_fundamentals", "600519", {})
    assert "Lightweight Fundamentals" in result
    # quote 真實資料
    assert "24.5" in result  # PE
    assert "0.12" in result  # 周轉率


# ============================================================
# 5. get_balance_sheet → 真實資產負債
# ============================================================

def test_get_balance_sheet_returns_real_equity_and_leverage():
    """get_balance_sheet 返回真實淨資產 + 資產負債率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_balance_sheet", "600519", {})
    assert "Balance Sheet" in result
    # 淨資產 2800 億
    assert "2800.00 億" in result or "2900.00 億" in result
    # 資產負債率 ~18%
    assert "18.00%" in result or "17.50%" in result


# ============================================================
# 6. get_cashflow → 真實經營現金流
# ============================================================

def test_get_cashflow_returns_real_operating_cashflow():
    """get_cashflow 返回真實經營現金流量淨額(800 億)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "600519", {})
    assert "Cash Flow Statement" in result
    # 經營現金流 800 億
    assert "800.00 億" in result or "220.00 億" in result
    # 每股現金流 ~63 元
    assert "63.60" in result or "17.50" in result


def test_get_cashflow_does_not_match_capital_flow_branch():
    """關鍵 bug 迴歸:cashflow 不能被路由到"資金流"分支
    (上次 bug:method 含 'flow' 字串就誤判為資金流向)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "600519", {})
    # 資金流分支會返回 "No capital flow data" — 不應該出現
    assert "No capital flow data" not in result
    # 應該是現金流量表
    assert "Cash Flow" in result


# ============================================================
# 7. get_income_statement → 真實利潤表
# ============================================================

def test_get_income_statement_returns_real_revenue_and_profit():
    """get_income_statement 返回真實營業收入 + 淨利潤 + 毛利率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_income_statement", "600519", {})
    assert "Income Statement" in result
    # 營收 1800 億
    assert "1800.00 億" in result or "500.00 億" in result
    # 毛利率 91%
    assert "91.00%" in result or "91.50%" in result


# ============================================================
# 8. Stock metadata header — 公司名永遠在,LLM 不會瞎編
# ============================================================

def test_all_tools_include_stock_metadata_header():
    """所有工具的輸出都帶 [Stock Metadata] 公司名資訊"""
    methods = [
        "get_stock_data", "get_news", "get_global_news",
        "get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement",
    ]
    with panwatch_data_context(_full_ctx()):
        for m in methods:
            args = ("600519", "macd") if m == "get_indicators" else ("600519",)
            result = _serve_from_panwatch(m, "600519", {}, args=args)
            assert "貴州茅臺" in result or "600519" in result, f"{m} 缺少公司元資訊"
            assert "Stock Metadata" in result or "Technical Indicator" in result, f"{m} 缺少 metadata header"
