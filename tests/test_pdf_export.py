"""詳細資訊報告匯出 PDF(後臺直出:xhtml2pdf + reportlab STSong-Light 中文字型)。"""

from __future__ import annotations

import io


def _weasyprint_renders() -> bool:
    """WeasyPrint 能否真正渲染(需 pango 等系統庫)。不可用時回退 xhtml2pdf,中文走 CID 字型不進文本層。"""
    try:
        from weasyprint import HTML

        HTML(string="<p>測試</p>").write_pdf()
        return True
    except Exception:
        return False


_WEASY = _weasyprint_renders()


def test_render_pdf_returns_valid_bytes_with_chinese():
    """markdown→PDF:返回合法 PDF 位元組,且中文進入文本層(非豆腐塊、可複製)。"""
    from src.modules.reporting.pdf_export import render_analysis_pdf

    md = "# 廣汽集團(601238)深度分析\n\n**最終決策:持有**\n\n- 多頭:業績拐點確認\n- 空頭:估值偏高"
    data = render_analysis_pdf("【深度】廣汽集團(601238):持有", md)
    assert isinstance(data, (bytes, bytearray))
    assert bytes(data[:4]) == b"%PDF"
    assert len(data) > 1500

    if not _WEASY:
        return  # xhtml2pdf 回退:中文走 STSong-Light CID,不進文本層;僅 WeasyPrint 路徑保證可複製中文

    from pypdf import PdfReader

    txt = PdfReader(io.BytesIO(bytes(data))).pages[0].extract_text() or ""
    assert "廣汽集團" in txt
    assert "持有" in txt


def test_render_pdf_handles_empty_markdown():
    """空正文也不崩,仍返回合法 PDF(至少有標題)。"""
    from src.modules.reporting.pdf_export import render_analysis_pdf

    data = render_analysis_pdf("標題", "")
    assert bytes(data[:4]) == b"%PDF"


def test_assemble_report_markdown_mirrors_detail_page_sections():
    """從 raw_data 拼出的報告含詳細資訊頁全部分節:PM/交易員/4分析師全文/多空辯論全文/風控辯論全文。"""
    from src.modules.reporting.pdf_export import assemble_report_markdown

    raw = {
        "suggestion": {"action_label": "持有", "confidence": 5.0},
        "final_decision": "PM決策正文XYZ",
        "trader_plan": "交易員計劃正文XYZ",
        "analyst_reports": {
            "market": "技術面分析正文XYZ", "social": "情緒面分析正文XYZ",
            "news": "新聞面分析正文XYZ", "fundamentals": "基本面分析正文XYZ",
        },
        "debate_history": {"history": "多頭觀點AAA 空頭觀點BBB", "judge_decision": "研究主管裁決XYZ"},
        "risk_debate": {"history": "激進CCC 保守DDD", "judge_decision": "風控裁決XYZ"},
    }
    md = assemble_report_markdown(raw)
    for must in [
        "PM決策正文XYZ", "交易員計劃正文XYZ",
        "技術面分析正文XYZ", "情緒面分析正文XYZ", "新聞面分析正文XYZ", "基本面分析正文XYZ",
        "多頭觀點AAA", "空頭觀點BBB", "研究主管裁決XYZ",
        "激進CCC", "風控裁決XYZ",
        "技術分析師", "看多看空辯論", "風控辯論",
    ]:
        assert must in md, f"缺少: {must}"


def _mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import src.platform.persistence.models  # noqa: F401
    from src.platform.persistence.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_pdf_endpoint_returns_full_detail_content():
    """端點:返回 application/pdf 附件,且含詳細資訊頁完整內容(分析師/辯論全文,來自 raw_data,非僅 content 摘要)。"""
    from src.modules.automation.api import agents
    from src.platform.persistence.models import AnalysisHistory

    db = _mem_db()
    try:
        db.add(AnalysisHistory(
            agent_name="tradingagents", stock_symbol="601238",
            analysis_date="2026-06-20", title="【深度】廣汽集團(601238):持有",
            content="# 摘要\n\n**持有**",  # content 是精簡版,不含下面這些
            raw_data={
                "suggestion": {"action_label": "持有", "confidence": 5.0},
                "final_decision": "PM決策正文",
                "analyst_reports": {"market": "技術面分析正文UNIQUE", "fundamentals": "基本面正文"},
                "debate_history": {"history": "多頭觀點UNIQUE 空頭觀點", "judge_decision": "研究主管裁決"},
                "risk_debate": {"history": "激進 保守", "judge_decision": "風控裁決"},
            },
        ))
        db.commit()
        resp = agents.export_tradingagents_analysis_pdf(
            stock_symbol="601238", analysis_date="2026-06-20", db=db)
        assert resp.media_type == "application/pdf"
        assert bytes(resp.body[:4]) == b"%PDF"
        assert "attachment" in resp.headers["content-disposition"]

        if not _WEASY:
            return  # 中文文本層僅 WeasyPrint 路徑可提取;content 組裝由 test_assemble_* 覆蓋

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(bytes(resp.body)))
        txt = "\n".join((p.extract_text() or "") for p in reader.pages)
        # content 摘要裡沒有的「分析師全文 / 辯論全文」確實進了 PDF
        assert "技術面分析正文UNIQUE" in txt
        assert "多頭觀點UNIQUE" in txt
    finally:
        db.close()


def test_pdf_endpoint_404_when_missing():
    """端點:無記錄 → HTTP 404。"""
    import pytest
    from fastapi import HTTPException

    from src.modules.automation.api import agents

    db = _mem_db()
    try:
        with pytest.raises(HTTPException) as ei:
            agents.export_tradingagents_analysis_pdf(
                stock_symbol="000000", analysis_date="2026-06-20", db=db)
        assert ei.value.status_code == 404
    finally:
        db.close()
