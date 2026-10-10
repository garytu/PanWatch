"""上游 5 檔評級 → PanWatch 3 檔 action 映射迴歸測試。

根因 bug:上游 PM 用 Buy/Overweight/Hold/Underweight/Sell 五檔,我們只識別 3 檔,
Overweight/Underweight 被兜底到 hold,導致頂層顯示"持有"但 PM 實際給出"減持"。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.modules.automation.tradingagents.decision import (
    RATING_ACTION_MAP,
    RATING_LABEL_MAP,
    _parse_rating_from_text,
    _parse_rating_label,
    map_state_to_result,
)


def _stock():
    return SimpleNamespace(symbol="601127", name="賽力斯", market=SimpleNamespace(value="CN"))


def _result(decision_raw: str, final_decision_text: str = "") -> dict:
    """構造一份 ta_result 給 map_state_to_result 用"""
    return {
        "decision": decision_raw,
        "final_state": {
            "final_trade_decision": final_decision_text,
            "trader_investment_plan": "Action: Sell\n\nReasoning: 風險大",
        },
        "cost_usd": 0.05,
    }


# ============================================================
# 5 檔評級 → 3 檔 action + 中文標籤
# ============================================================

def test_buy_rating_maps_to_buy():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Buy"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "買入"


def test_overweight_rating_maps_to_buy_with_zh_label():
    """Overweight(增持) → action=buy,但 label 顯示"增持"區分於 buy"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "增持"
    assert r.raw_data["suggestion"]["rating_raw"] == "overweight"


def test_hold_rating_maps_to_hold():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold"))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["action_label"] == "持有"


def test_underweight_rating_maps_to_sell_with_zh_label():
    """關鍵 bug 迴歸:Underweight(減持) 之前被錯誤兜底到 hold,
    現在應該 action=sell + label=減持"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Underweight"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "減持"
    assert r.raw_data["suggestion"]["rating_raw"] == "underweight"
    # 應觸發提醒(不是 hold)
    assert r.raw_data["suggestion"]["should_alert"] is True


def test_sell_rating_maps_to_sell():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Sell"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "賣出"


# ============================================================
# Fallback:propagate() 沒返回 5 檔,從文本里抽
# ============================================================

def test_decision_text_with_rating_label():
    """final_trade_decision 含 'Rating: Underweight' → 解析出 underweight"""
    text = "After thorough analysis...\n\n**Rating**: Underweight\n\nReason: high leverage"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_chinese_label():
    """中文'評級:減持' → underweight"""
    text = "綜合考慮:**評級:減持**,建議降低倉位"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_final_transaction_proposal():
    """'FINAL TRANSACTION PROPOSAL: SELL' → sell"""
    text = "...\n\nFINAL TRANSACTION PROPOSAL: **SELL**"
    assert _parse_rating_from_text(text) == "sell"


def test_decision_text_fallback_to_keyword_scan():
    """沒顯式標籤也能從文本里找到 5 檔詞"""
    text = "Based on macro headwinds, recommend Overweight position in defensive sectors."
    assert _parse_rating_from_text(text) == "overweight"


def test_decision_empty_falls_back_to_hold():
    """propagate 返回空 + 文本也沒評級詞 → 預設 hold"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("", final_decision_text=""))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_review_signal_is_preserved_as_manual_review():
    """0.4.0 的 REVIEW 是不可交易的人工複核訊號，不能偽裝成普通持有。"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="上游無法解析最終評級"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"  # 保持現有前端 3 檔 API
    assert suggestion["action_label"] == "待人工複核"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["should_alert"] is True
    assert suggestion["upstream_decision"] == "review"


def test_review_signal_overrides_parseable_pm_rating():
    """上游 REVIEW 優先於正文中的評級詞，絕不能轉成可交易買入。"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="評級：買入"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"
    assert suggestion["action_label"] == "待人工複核"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["review_required"] is True


def test_decision_unrecognized_then_text_has_underweight():
    """propagate 返回 'xxxx' 不識別 → 從 final_decision 文本抽 underweight"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("xxxx", final_decision_text="...\n**Rating**: Underweight\n..."),
    )
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "減持"


# ============================================================
# 正文與上游 decision 衝突:正文為準(生產 bug 迴歸)
# 上游 propagate 二次提煉出 "HOLD",但 PM 正文白紙黑字寫"賣出/買入",
# 必須以正文為準。真實中文 PM 正文用全形標點(：),早期正則只認半形(:)
# 導致"最終交易決策：Buy"匹配不到、仍回退到失真的 decision=HOLD 顯示"持有"。
# 這裡全形/半形都覆蓋。
# ============================================================

def test_fullwidth_colon_buy_overrides_hold():
    """生產 case(廣汽 601238):decision=HOLD 但正文全形'最終交易決策： Buy' → 必須 buy"""
    text = (
        "尊敬的各位投資決策者,經過對廣汽集團(601238)的風險分析師辯論進行綜合分析,"
        "以下是我對最終交易決策的建議:\n\n"
        "**最終交易決策： Buy**\n\n**決策依據：** 1. 盈利能力分析..."
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("HOLD", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "買入"


def test_fullwidth_colon_sell_overrides_hold():
    """全形冒號 + 中文:decision=Hold 但正文'最終交易決策：賣出' → sell"""
    text = "綜合風險辯論...\n\n## 最終交易決策：**賣出**\n\n### 評級：**Sell**\n\n核心依據..."
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "賣出"
    assert r.raw_data["suggestion"]["rating_raw"] == "sell"


def test_halfwidth_colon_still_works():
    """半形冒號也要繼續工作:'最終交易決策: 買入' → buy"""
    text = "總之...\n\n最終交易決策: **買入**\n評級: 買入"
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"


def test_parse_rating_label_covers_both_colons():
    """_parse_rating_label 全形(：)半形(:)冒號都能解析"""
    assert _parse_rating_label("最終交易決策：Buy") == "buy"   # 全形
    assert _parse_rating_label("最終交易決策: Buy") == "buy"   # 半形
    assert _parse_rating_label("評級：賣出") == "sell"          # 全形中文
    assert _parse_rating_label("評級: Sell") == "sell"         # 半形
    assert _parse_rating_label("FINAL TRANSACTION PROPOSAL: **BUY**") == "buy"


def test_decision_used_when_text_has_no_label():
    """正文沒有顯式評級標籤 → 回退信任上游 decision(此處 Hold)"""
    text = "市場存在不確定性,建議觀察。維持觀望立場,等待更明確訊號。"
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_text_label_not_confused_by_distractor_words():
    """正文含幹擾詞(否決了'買入')但顯式標籤是'賣出' → 標籤優先,sell 而非 buy"""
    text = (
        "我否決了多頭分析師的**買入**建議,理由是基本面惡化。\n\n"
        "FINAL TRANSACTION PROPOSAL: **SELL**"
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"


# ============================================================
# Markdown 渲染:5 檔評級標籤寫進 markdown 頭部
# ============================================================

def test_markdown_shows_5_tier_rating_in_header():
    """Markdown 頂部應顯示原始 5 檔評級,避免"建議賣出但頂部寫持有"的歧義"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("Underweight", final_decision_text="Rating: Underweight\n\nReason: ..."),
    )
    assert "減持" in r.content
    # 既要有 action_label,也要有 rating note
    assert r.content.count("減持") >= 1


# ============================================================
# raw_data 裡同時保留 3 檔(decision) + 5 檔(rating)
# ============================================================

def test_raw_data_has_both_decision_and_rating():
    """前端相容:既要有 3 檔 decision 給老程式碼,也要有 5 檔 rating 給新展示"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["decision"] == "buy"  # 3 檔
    assert r.raw_data["rating"] == "overweight"  # 5 檔


# ============================================================
# 靜態 mapping 完整性
# ============================================================

def test_all_5_ratings_have_label():
    for r in ("buy", "overweight", "hold", "underweight", "sell"):
        assert r in RATING_LABEL_MAP
        assert r in RATING_ACTION_MAP


def test_action_map_only_uses_3_actions():
    """3 檔 action 只能是 buy/hold/sell(前端型別)"""
    assert set(RATING_ACTION_MAP.values()) == {"buy", "hold", "sell"}


# ============================================================
# Markdown 完整性:9 個 Agent 的產出都體現
# ============================================================

def _full_state():
    return {
        "final_trade_decision": "**Rating: Underweight** 詳細決策書...",
        "trader_investment_plan": "Action: Sell\n建議減碼 70%",
        "risk_judge_decision": "風控辯論:激進/保守/中立討論後,建議謹慎",
        "investment_debate_state": {
            "history": "Bull: ...\nBear: ...\nBull: ...\nBear: ...",
            "judge_decision": "研究主管:綜合看多看空雙方,傾向謹慎持有",
        },
        "market_report": "技術面:MACD 死叉,空頭排列,趨勢偏弱..." * 30,
        "social_report": "情緒面:討論度下降,看空聲音增加..." * 20,
        "news_report": "新聞面:公告:Q1 淨利潤下降..." * 20,
        "fundamentals_report": "基本面:營收增長但毛利下降,ROE 轉負..." * 20,
    }


def test_markdown_contains_decision_chain():
    """markdown 主體含決策鏈(PM/交易員/研究主管/風控);分析師完整報告移到 raw_data 由前端 tab 渲染"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Underweight", "final_state": _full_state(), "cost_usd": 0.05},
    )
    content = r.content
    assert "PM 最終決策書" in content
    assert "交易員執行計劃" in content
    assert "研究主管裁決" in content
    assert "傾向謹慎持有" in content
    assert "風控辯論裁決" in content
    # 不再把分析師概覽塞進主體(早先截 300 字會把財務表格截在表頭)
    assert "4 位分析師觀點概覽" not in content
    # 完整分析師報告在 raw_data,前端 tab 渲染
    reports = r.raw_data["analyst_reports"]
    assert reports["market"] and reports["social"] and reports["news"] and reports["fundamentals"]


def test_analyst_reports_full_not_truncated():
    """分析師報告在 raw_data 裡完整保留、不截斷(修復財務表格被截在表頭)"""
    state = _full_state()
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    # 完整等於原始報告,無任何截斷
    assert reports["market"] == state["market_report"]
    assert reports["fundamentals"] == state["fundamentals_report"]
    assert len(reports["market"]) > 300  # 遠超舊的 300 字概覽上限


def test_empty_analyst_kept_empty_in_raw_data():
    """某位分析師沒產出 → raw_data 裡為空串(前端 tab 跳過該 tab)"""
    state = _full_state()
    state["social_report"] = ""  # 情緒分析師沒跑
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    assert reports["social"] == ""
    assert reports["market"] and reports["news"] and reports["fundamentals"]


def test_markdown_skips_judge_when_no_debate():
    """沒辯論歷史時不渲染'研究主管裁決' section"""
    state = _full_state()
    state["investment_debate_state"] = {"history": "", "judge_decision": ""}
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    assert "研究主管裁決" not in r.content


# ============================================================
# 情緒分析師欄位(上游 sentiment_report) + 通知完整內容
# ============================================================

def test_sentiment_report_maps_to_social():
    """上游情緒欄位是 sentiment_report,必須對映到 analyst_reports.social(修復看不到情緒分析師)"""
    state = {"final_trade_decision": "評級：買入", "sentiment_report": "情緒面:討論度上升,看多增加"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "情緒面:討論度上升,看多增加"


def test_social_report_fallback_when_no_sentiment():
    """舊欄位 social_report 仍相容(無 sentiment_report 時回退)"""
    state = {"final_trade_decision": "評級：持有", "social_report": "舊情緒欄位內容"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "舊情緒欄位內容"


def test_notify_content_only_final_decision():
    """通知體(notify_content)只放「最終決策」:決策摘要 + PM 最終決策書。
    交易員執行計劃 / 研究主管裁決 / 風控辯論 / 四位分析師都不進通知(避免過長被截斷),
    只在詳細資訊頁;content(完整決策鏈)仍保留供歷史/詳細資訊。"""
    state = {
        "final_trade_decision": "最終交易決策：買入\n\n核心邏輯:基本面拐點確認,估值修復在即",
        "trader_investment_plan": "交易員計劃:分三批建倉,首筆倉位 30%",
        "market_report": "技術分析詳細內容" * 100,
        "sentiment_report": "情緒面詳細" * 100,
        "investment_debate_state": {"history": "多空辯論歷史正文", "judge_decision": "研究主管裁決:傾向看多"},
        "risk_debate_state": {"history": "風控三方辯論正文", "judge_decision": "風控團隊結論:倉位可控"},
    }
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    # 通知體單獨設定(不再回退 content)
    assert r.notify_content is not None
    nc = r.notify_content
    # 含最終決策核心(決策摘要 + PM 決策書正文)
    assert "最終決策" in nc
    assert "買入" in nc
    assert "基本面拐點確認" in nc
    # 不含交易員計劃 / 裁決 / 風控 / 分析師明細的具體內容
    assert "分三批建倉" not in nc
    assert "傾向看多" not in nc
    assert "倉位可控" not in nc
    assert state["market_report"] not in nc
    # content(完整)仍含決策鏈(供詳細資訊頁/歷史)
    assert "PM 最終決策書" in r.content
    assert "交易員執行計劃" in r.content


# ============================================================
# 置信度 A+B:優先抓 PM 顯式數字(含全形冒號),抓不到按評級推導
# ============================================================

def test_confidence_extracted_fullwidth_colon():
    """全形'置信度：8.5/10'能抓到真實值(早先只認半形冒號一律回退預設)"""
    state = {"final_trade_decision": "評級：買入\n置信度：8.5/10"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 8.5


def test_confidence_extracted_halfwidth():
    """半形'confidence: 7'仍能抓到"""
    state = {"final_trade_decision": "Rating: Buy\nconfidence: 7"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 7.0


def test_confidence_derived_from_rating_when_absent():
    """無顯式置信度 → 按評級推導(強方向>中性),不再一律 5.0"""
    buy = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": {"final_trade_decision": "最終交易決策：買入"}, "cost_usd": 0.01})
    hold = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": {"final_trade_decision": "最終交易決策：持有"}, "cost_usd": 0.01})
    sell = map_state_to_result(stock=_stock(), ta_result={"decision": "Sell", "final_state": {"final_trade_decision": "評級：賣出"}, "cost_usd": 0.01})
    assert buy.raw_data["suggestion"]["confidence"] == 7.0
    assert hold.raw_data["suggestion"]["confidence"] == 5.0
    assert sell.raw_data["suggestion"]["confidence"] == 7.0
    assert buy.raw_data["suggestion"]["confidence"] > hold.raw_data["suggestion"]["confidence"]
