import type { DeepAnalysisResult } from '@panwatch/api'

export interface AnalysisSection {
  id: string
  title: string
  markdown: string
}

/**
 * 從深度分析 raw_data 組裝各部分(決策正文 / 四分析師 / 看多看空辯論 / 風控辯論)。
 * 彈跳視窗的 tab 與詳細閱讀頁的長文共用這一份組裝邏輯,避免兩處渲染漂移。
 * 順序即詳細頁從上到下、彈跳視窗 tab 從左到右的順序。只返回有內容的部分。
 */
export function buildAnalysisSections(
  rawData: Partial<DeepAnalysisResult['raw_data']>,
): AnalysisSection[] {
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const riskDebate = rawData.risk_debate
  const sections: AnalysisSection[] = []

  // 決策書:section 標題直接用「PM 最終決策書」(去掉原先重複的前置「最終決策」標題);
  // 交易員執行計劃作為子標題保留(與決策書區分)。
  const decisionBody = [
    rawData.final_decision || '',
    rawData.trader_plan && `### 💼 交易員執行計劃\n\n${rawData.trader_plan}`,
  ]
    .filter(Boolean)
    .join('\n\n')
  if (decisionBody) sections.push({ id: 'decision', title: 'PM 最終決策書', markdown: decisionBody })

  // 四位分析師
  const analysts: [string, string][] = [
    ['market', '技術分析師'],
    ['social', '情緒分析師'],
    ['news', '新聞分析師'],
    ['fundamentals', '基本面分析師'],
  ]
  for (const [k, title] of analysts) {
    const text = (reports as unknown as Record<string, string>)[k] || ''
    if (text) sections.push({ id: k, title, markdown: text })
  }

  // 看多看空辯論(研究團隊:辯論歷史 + 研究主管裁決)
  if (debate?.history) {
    let dc = debate.history
    if (debate.judge_decision) dc += `\n\n### ⚖️ 研究主管裁決\n\n${debate.judge_decision}`
    sections.push({ id: 'debate', title: '看多看空辯論', markdown: dc })
  }

  // 風控辯論(風控團隊:激進/中立/保守辯論 + 風控裁決)
  if (riskDebate?.history) {
    let rc = riskDebate.history
    if (riskDebate.judge_decision) rc += `\n\n### 🛡️ 風控裁決\n\n${riskDebate.judge_decision}`
    sections.push({ id: 'risk', title: '風控辯論', markdown: rc })
  }

  return sections
}
