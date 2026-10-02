// Map Python module logger names to concise Chinese display names
export const LOGGER_MAPPING: Record<string, string> = {
  // Agents
  'src.agents.daily_report': '收盤覆盤',
  'src.agents.premarket_outlook': '盤前分析',
  'src.agents.intraday_monitor': '盤中監測',
  'src.agents.base': 'Agent執行鏈路',
  'src.agents.news_digest': '新聞速遞',
  'src.agents.chart_analyst': '技術分析',
  'src.agents.tradingagents': '深度分析',
  'src.agents.tradingagents.agent': '深度分析-主流程',
  'src.agents.tradingagents.observability': '深度分析-進度與成本',
  'src.agents.tradingagents.data_context': '深度分析-資料上下文',
  'src.agents.tradingagents.toolkit_adapter': '深度分析-資料適配',
  'src.agents.tradingagents.decision': '深度分析-決策與模擬交易',
  'src.agents.tradingagents.runtime_support': '深度分析-執行時相容',
  'src.agents.tradingagents.operations': '深度分析-觸發與評估',
  'tradingagents': '深度分析(上游)',

  // Core
  'src.core.scheduler': '排程器',
  'src.core.ai_client': 'AI使用者端',
  'src.core.notifier': '通知',
  'src.core.analysis_history': '分析歷史',
  'src.core.suggestion_pool': '建議池',
  'src.core.data_collector': '資料採集',

  // Collectors
  'src.collectors.akshare_collector': '行情采集',
  'src.collectors.kline_collector': 'K線採集',
  'src.collectors.capital_flow_collector': '資金流採集',
  'src.collectors.news_collector': '新聞採集',
  'src.collectors.screenshot_collector': '截圖採集',

  // Web/API
  'src.web.api': 'API',
  'src.web.app': 'Web應用',
  'src.web.database': '資料庫',
  'src.web.stock_list': '股票列表',
  'api': 'API',

  // Entry
  'server': '服務',

  // Third-party & infra
  'httpx': 'HTTP使用者端',
  'httpcore': 'HTTP核心',
  'urllib3': 'HTTP庫',
  'requests': 'HTTP使用者端',
  'uvicorn.access': '訪問日誌',
  'uvicorn.error': 'Uvicorn錯誤',
  'uvicorn': 'Uvicorn',
  'fastapi': 'FastAPI',
  'starlette': 'Starlette',
  'sqlalchemy.engine': '資料庫引擎',
  'sqlalchemy': 'SQLAlchemy',
  'apscheduler': 'APScheduler',
  'playwright': '瀏覽器',
  'openai': 'AI SDK',
  'tenacity': '重試庫',
}

export function mapLoggerName(moduleName?: string): string {
  if (!moduleName) return ''
  let bestKey = ''
  for (const key of Object.keys(LOGGER_MAPPING)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return LOGGER_MAPPING[bestKey] || moduleName
}

export function loggerOptions(): { key: string, label: string }[] {
  return Object.entries(LOGGER_MAPPING).map(([key, label]) => ({ key, label }))
}
