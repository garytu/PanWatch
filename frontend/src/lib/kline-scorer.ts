import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'

export type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

export interface KlineEvidenceItem {
  text: string
  details?: string
  delta: number
  tag?: string
}

export interface KlineScoreSuggestion {
  action: Action
  action_label: string
  signal: string
  score: number
  evidence: KlineEvidenceItem[]
  tags: string[]
}

export function buildKlineSuggestion(s: KlineSummaryData, holding?: boolean): KlineScoreSuggestion {
  let score = 0
  const items: KlineEvidenceItem[] = []
  const tags: string[] = []

  const fmt = (n?: number | null, digits: number = 2): string => {
    if (n == null || Number.isNaN(n)) return '--'
    return Number(n).toFixed(digits)
  }

  const tf = s.timeframe || '1d'
  const asof = s.asof ? `截至${s.asof}` : ''

  const addItem = (text: string, delta: number = 0, tag?: string, details?: string) => {
    items.push({ text, delta, tag, details })
    score += delta
    if (tag) tags.push(tag)
  }

  // Trend
  if (s.trend?.includes('多頭')) {
    addItem('均線多頭排列，趨勢偏強', 2, '多頭', `週期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('空頭')) {
    addItem('均線空頭排列，趨勢偏弱', -2, '空頭', `週期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  } else if (s.trend?.includes('交織')) {
    addItem('均線交織，趨勢不明', 0, undefined, `週期${tf} ${asof} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`)
  }

  // MACD
  if (s.macd_status?.includes('金叉')) {
    addItem('MACD 金叉，短線動能偏強', 2, 'MACD金叉', `週期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_status?.includes('死叉')) {
    addItem('MACD 死叉，短線動能轉弱', -2, 'MACD死叉', `週期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_hist != null) {
    if (s.macd_hist > 0.0) {
      addItem('MACD 柱體為正（動能偏多）', 1, undefined, `週期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    } else if (s.macd_hist < 0.0) {
      addItem('MACD 柱體為負（動能偏空）', -1, undefined, `週期${tf} ${asof} · hist: ${fmt(s.macd_hist, 3)}`)
    }
  }

  // RSI
  if (s.rsi_status?.includes('超賣')) {
    addItem('RSI 超賣，可能存在反彈', 1, 'RSI超賣', `週期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（閾值<20）`)
  } else if (s.rsi_status?.includes('偏強')) {
    addItem('RSI 偏強，買盤佔優', 1, 'RSI偏強', `週期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（閾值70-80）`)
  } else if (s.rsi_status?.includes('超買')) {
    addItem('RSI 超買，注意回撥風險', -1, 'RSI超買', `週期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（閾值>80）`)
  } else if (s.rsi_status?.includes('偏弱')) {
    addItem('RSI 偏弱，短線承壓', -1, 'RSI偏弱', `週期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}（閾值<30）`)
  } else if (s.rsi_status?.includes('中性')) {
    addItem('RSI 中性', 0, undefined, `週期${tf} ${asof} · RSI6: ${fmt(s.rsi6, 1)}`)
  }

  // KDJ
  if (s.kdj_status?.includes('金叉')) {
    addItem('KDJ 金叉，短線轉強', 1, 'KDJ金叉', `週期${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }
  if (s.kdj_status?.includes('死叉')) {
    addItem('KDJ 死叉，短線轉弱', -1, 'KDJ死叉', `週期${tf} ${asof} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`)
  }

  // BOLL
  if (s.boll_status?.includes('突破上軌')) {
    addItem('突破布林上軌，趨勢強勢', 1, '突破上軌', `週期${tf} ${asof} · close: ${fmt(s.last_close)} · 上軌: ${fmt(s.boll_upper)}`)
  } else if (s.boll_status?.includes('跌破下軌')) {
    addItem('跌破布林下軌，走勢偏弱', -1, '跌破下軌', `週期${tf} ${asof} · close: ${fmt(s.last_close)} · 下軌: ${fmt(s.boll_lower)}`)
  }

  // Volume
  if (s.volume_trend?.includes('放量')) {
    addItem('放量配合，資金參與度提升', 1, '放量', `週期${tf} ${asof} · 量比: ${fmt(s.volume_ratio, 1)}x`)
  } else if (s.volume_trend?.includes('縮量')) {
    addItem('縮量，動能不足', -1, '縮量', `週期${tf} ${asof} · 量比: ${fmt(s.volume_ratio, 1)}x`)
  }

  // Support / Resistance proximity
  if (s.last_close != null && s.support != null && s.support > 0) {
    if (s.last_close <= s.support * 1.02) {
      const dist = (s.last_close - s.support) / s.support * 100
      addItem('價格接近支撐位，止跌反彈機率提升', 1, '靠近支撐', `週期${tf} ${asof} · close: ${fmt(s.last_close)} · 支撐: ${fmt(s.support)} · 距離: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}%（閾值<=+2%）`)
    }
  }
  if (s.last_close != null && s.resistance != null && s.resistance > 0) {
    if (s.last_close >= s.resistance * 0.98) {
      const dist = (s.last_close - s.resistance) / s.resistance * 100
      addItem('價格接近壓力位，上行空間受限', -1, '靠近壓力', `週期${tf} ${asof} · close: ${fmt(s.last_close)} · 壓力: ${fmt(s.resistance)} · 距離: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}%（閾值>=-2%）`)
    }
  }

  const holdingFlag = holding === true
  let action: Action
  if (holdingFlag) {
    if (score >= 3) action = 'add'
    else if (score >= 1) action = 'hold'
    else if (score <= -3) action = 'sell'
    else if (score <= -1) action = 'reduce'
    else action = 'watch'
  } else {
    if (score >= 3) action = 'buy'
    else if (score <= -2) action = 'avoid'
    else action = 'watch'
  }

  const uniqTags = Array.from(new Set(tags))
  const signal = uniqTags.length > 0 ? uniqTags.join(' / ') : '技術面中性'

  const actionLabel = (a: Action): string => {
    switch (a) {
      case 'buy': return '買入'
      case 'add': return '加碼'
      case 'reduce': return '減碼'
      case 'sell': return '賣出'
      case 'hold': return '持有'
      case 'watch': return '觀望'
      case 'avoid': return '迴避'
      default: return '觀望'
    }
  }

  return {
    action,
    action_label: actionLabel(action),
    signal,
    score,
    evidence: items,
    tags: uniqTags,
  }
}
