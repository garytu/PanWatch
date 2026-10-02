"""chat 工具迴圈 golden set。

覆蓋：5 個工具各若干場景、多工具組合、不該調工具的閒聊/概念題、
工具失敗降級。有據性斷言的關鍵值全部來自 mock 資料（模型沒見過就寫不出）。

維護約定：每個線上 bad case 修復後固化一條新用例。
"""

from tests.eval.framework import ChatEvalCase

# ──────────────── mock 工具資料 ────────────────
# 數值刻意取"模型編不出來"的非整值，answer_must_contain 據此驗證有據性

MOCK_PORTFOLIO = (
    "實盤持倉：\n"
    "- 貴州茅臺(CN:600519) 100股 成本1503.2 風格波段\n\n"
    "模擬交易持倉：\n"
    "- 寧德時代(CN:300750) 200股 入場價211.4 停損196.0 未實現獲利1284.0"
)
MOCK_PORTFOLIO_EMPTY = "使用者暫無持倉。"
MOCK_QUOTE_600519 = "即時行情：貴州茅臺（CN:600519）價格 1712.5，漲跌幅 1.35%，成交量 28143"
MOCK_QUOTE_00700 = "即時行情：騰訊控股（HK:00700）價格 402.8，漲跌幅 -0.62%，成交量 1834萬"
MOCK_QUOTE_TSLA = "即時行情：Tesla（US:TSLA）價格 251.37，漲跌幅 2.14%，成交量 9812萬"
MOCK_TA_600519 = "技術面：趨勢 上行，MACD 金叉，RSI 58.2，支撐位 1651.0，壓力位 1783.0"
MOCK_TA_300750 = "技術面：趨勢 震盪，MACD 死叉，RSI 44.1，支撐位 198.3，壓力位 226.5"
MOCK_TA_000858 = "技術面：趨勢 下行，MACD 死叉，RSI 38.5，支撐位 128.6，壓力位 145.2"
MOCK_SUGGESTIONS_600519 = (
    "最近 AI 建議：\n"
    "- [收盤覆盤] 減碼: 高位滯漲，量能持續萎縮\n"
    "- [盤前分析] 持有: 均線多頭排列，等待放量"
)
MOCK_WATCHLIST = (
    "自選股列表：\n"
    "- 貴州茅臺(CN:600519)\n"
    "- 寧德時代(CN:300750)\n"
    "- 騰訊控股(HK:00700)"
)
TOOL_FAIL_TIMEOUT = "工具執行出錯: 資料來源請求超時"

# 失敗場景通用：答案裡應有"如實說明失敗"類表述
FAIL_PHRASES = ("失敗", "無法", "未能", "暫時", "出錯", "稍後", "獲取不到", "拿不到")


CHAT_CASES: list[ChatEvalCase] = [
    # ──────── get_portfolio ────────
    ChatEvalCase(
        id="portfolio-1",
        question="我的持倉怎麼樣？幫我看看健康度",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        answer_must_contain=("茅臺",),
        notes="持倉健康類問題應主動查持倉，且答案引用真實持倉",
    ),
    ChatEvalCase(
        id="portfolio-2",
        question="我現在模擬交易未實現獲利多少？",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        answer_must_contain=("1284",),
        notes="未實現獲利數字必須來自工具返回，驗證有據性",
    ),
    ChatEvalCase(
        id="portfolio-3",
        question="幫我看看該不該調倉",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        notes="調倉建議前必須先獲取持倉",
    ),
    # ──────── get_stock_quote ────────
    ChatEvalCase(
        id="quote-1",
        question="600519 現在多少錢？",
        tool_data={"get_stock_quote": MOCK_QUOTE_600519},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "600519"}},
        answer_must_contain=("1712.5",),
        notes="價格必須引用工具返回值",
    ),
    ChatEvalCase(
        id="quote-2",
        question="港股騰訊（00700）現在股價如何？",
        tool_data={"get_stock_quote": MOCK_QUOTE_00700},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "00700", "market": "HK"}},
        answer_must_contain=("402.8",),
        notes="市場引數應正確傳 HK",
    ),
    ChatEvalCase(
        id="quote-3",
        question="美股特斯拉 TSLA 今天漲了嗎？",
        tool_data={"get_stock_quote": MOCK_QUOTE_TSLA},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "TSLA", "market": "US"}},
        answer_must_contain=("2.14",),
        notes="漲跌幅必須引用工具返回值",
    ),
    # ──────── get_technical_analysis ────────
    ChatEvalCase(
        id="ta-1",
        question="600519 技術面怎麼樣？",
        tool_data={"get_technical_analysis": MOCK_TA_600519},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "600519"}},
        answer_must_contain=("金叉",),
        notes="技術面結論應引用工具資料",
    ),
    ChatEvalCase(
        id="ta-2",
        question="幫我看下 300750 的支撐位和壓力位",
        tool_data={"get_technical_analysis": MOCK_TA_300750},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "300750"}},
        answer_must_contain=("198.3", "226.5"),
        notes="支撐/壓力位數值必須來自工具返回",
    ),
    ChatEvalCase(
        id="ta-3",
        question="從 MACD 和 RSI 看，000858 現在是買點嗎？",
        tool_data={"get_technical_analysis": MOCK_TA_000858},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "000858"}},
        answer_must_contain=("38.5",),
        notes="RSI 數值必須來自工具返回",
    ),
    # ──────── get_stock_suggestions ────────
    ChatEvalCase(
        id="sugg-1",
        question="最近系統對 600519 給過什麼 AI 建議？",
        tool_data={"get_stock_suggestions": MOCK_SUGGESTIONS_600519},
        expected_tools=("get_stock_suggestions",),
        param_checks={"get_stock_suggestions": {"symbol": "600519"}},
        answer_must_contain=("減碼",),
        notes="歷史建議必須引用工具返回",
    ),
    ChatEvalCase(
        id="sugg-2",
        question="之前的分析報告怎麼評價 600519 的？",
        tool_data={"get_stock_suggestions": MOCK_SUGGESTIONS_600519},
        expected_tools=("get_stock_suggestions",),
        notes="歷史分析類問題應查建議庫而非編造",
    ),
    # ──────── get_watchlist ────────
    ChatEvalCase(
        id="watch-1",
        question="我的自選股有哪些？",
        tool_data={"get_watchlist": MOCK_WATCHLIST},
        expected_tools=("get_watchlist",),
        answer_must_contain=("寧德時代",),
        notes="自選列表必須來自工具返回",
    ),
    ChatEvalCase(
        id="watch-2",
        question="幫我看看自選裡有沒有港股",
        tool_data={"get_watchlist": MOCK_WATCHLIST},
        expected_tools=("get_watchlist",),
        answer_must_contain=("騰訊",),
        notes="需要基於自選列表判斷",
    ),
    # ──────── 多工具組合 ────────
    ChatEvalCase(
        id="multi-1",
        question="結合即時行情和技術面，幫我分析下 600519",
        tool_data={
            "get_stock_quote": MOCK_QUOTE_600519,
            "get_technical_analysis": MOCK_TA_600519,
        },
        expected_tools=("get_stock_quote", "get_technical_analysis"),
        answer_must_contain=("1712.5",),
        notes="組合分析應同時調兩個工具",
    ),
    ChatEvalCase(
        id="multi-2",
        question="我持倉裡的茅臺現在該停利嗎？先看下現價",
        tool_data={
            "get_portfolio": MOCK_PORTFOLIO,
            "get_stock_quote": MOCK_QUOTE_600519,
        },
        expected_tools=("get_portfolio", "get_stock_quote"),
        notes="停利判斷需要持倉成本 + 現價",
    ),
    ChatEvalCase(
        id="multi-3",
        question="把我自選股裡的茅臺行情報一下",
        tool_data={
            "get_watchlist": MOCK_WATCHLIST,
            "get_stock_quote": MOCK_QUOTE_600519,
        },
        expected_tools=("get_stock_quote",),
        answer_must_contain=("1712.5",),
        notes="至少要查行情；查不查自選列表均可接受",
    ),
    # ──────── 不該調工具的場景 ────────
    ChatEvalCase(
        id="chitchat-1",
        question="你好",
        expect_no_tools=True,
        notes="寒暄不該觸發任何工具",
    ),
    ChatEvalCase(
        id="chitchat-2",
        question="你是誰？你能幫我做什麼？",
        expect_no_tools=True,
        notes="自我介紹不該觸發工具",
    ),
    ChatEvalCase(
        id="chitchat-3",
        question="好的，謝謝你，再見",
        expect_no_tools=True,
        notes="致謝收尾不該觸發工具",
    ),
    ChatEvalCase(
        id="concept-1",
        question="什麼是市盈率？通俗解釋一下",
        expect_no_tools=True,
        notes="純概念解釋不需要即時資料",
    ),
    ChatEvalCase(
        id="concept-2",
        question="MACD 金叉是什麼意思？",
        expect_no_tools=True,
        notes="指標科普不需要調工具",
    ),
    # ──────── 工具失敗降級 ────────
    ChatEvalCase(
        id="fail-1",
        question="600519 現在多少錢？",
        tool_data={"get_stock_quote": TOOL_FAIL_TIMEOUT},
        expected_tools=("get_stock_quote",),
        answer_must_contain_any=FAIL_PHRASES,
        answer_must_not_contain=("1712.5",),
        notes="行情工具失敗：如實說明，不編造價格",
    ),
    ChatEvalCase(
        id="fail-2",
        question="000858 技術面如何？",
        tool_data={"get_technical_analysis": "未能獲取 CN:000858 的技術面資料。"},
        expected_tools=("get_technical_analysis",),
        answer_must_contain_any=FAIL_PHRASES,
        answer_must_not_contain=("38.5",),
        notes="技術面資料缺失：不編造指標數值",
    ),
    ChatEvalCase(
        id="fail-3",
        question="我的持倉怎麼樣？",
        tool_data={"get_portfolio": MOCK_PORTFOLIO_EMPTY},
        expected_tools=("get_portfolio",),
        answer_must_contain_any=("暫無", "沒有持倉", "無持倉", "空倉", "還沒有"),
        answer_must_not_contain=("茅臺",),
        notes="空持倉：如實告知，不編造持倉",
    ),
]
