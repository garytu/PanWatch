"""請求 / 回應 / 行情資料型別。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Request:
    """一次資料請求。frozen=True 便於做快取鍵。"""

    symbols: tuple[str, ...] = ()
    market: str = "CN"
    timeframe: str = "day"
    limit: int = 120
    since_hours: int = 12
    extra: tuple[tuple[str, Any], ...] = ()

    def cache_key(self, datatype: str) -> str:
        sym = ",".join(self.symbols)
        extra = ",".join(f"{k}={v}" for k, v in self.extra)
        return f"{datatype}|{self.market}|{self.timeframe}|{self.limit}|{self.since_hours}|{sym}|{extra}"


@dataclass
class Quote:
    """標準化即時報價。欄位對齊 _parse_tencent_line 的產出。"""

    symbol: str
    market: str
    current_price: float | None
    name: str = ""
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe_ratio: float | None = None
    circulating_market_value: float | None = None
    total_market_value: float | None = None
    timestamp: datetime | None = field(default_factory=datetime.now)
    instrument_id: str | None = None
    venue: str | None = None
    price_kind: str | None = None
    provider: str | None = None
    trade_date: str | None = None
    reference_price: float | None = None
    change_basis: str | None = None
    adjustment_mode: str | None = None
    availability: str | None = None
    freshness: dict = field(default_factory=dict)
    collection_health: dict = field(default_factory=dict)
    usable_for_trading: bool | None = None
    units: dict = field(default_factory=dict)
    volume_semantics: str | None = None
    eod_fallback: dict | None = None


@dataclass
class Bar:
    """標準化日K(對齊 PanWatch KlineData:date/open/close/high/low/volume)。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0
    provider: str | None = None
    adjustment_mode: str | None = None
    volume_unit: str | None = None


@dataclass
class CapitalFlow:
    """資金流向(對齊 PanWatch src/collectors/capital_flow_collector.CapitalFlow)。"""

    symbol: str
    name: str
    main_net_inflow: float | None = None      # 主力淨流入
    main_net_inflow_pct: float | None = None   # 主力淨流入佔比
    super_net_inflow: float | None = None      # 超大單淨流入
    big_net_inflow: float | None = None        # 大單淨流入
    mid_net_inflow: float | None = None        # 中單淨流入
    small_net_inflow: float | None = None      # 小單淨流入
    main_net_5d: float | None = None           # 5日主力淨流入
    flow_kind: str = "large_order_cash"
    unit: str = "currency"
    trade_date: str | None = None
    foreign_net_shares: int | None = None
    trust_net_shares: int | None = None
    dealer_net_shares: int | None = None
    institutional_net_shares: int | None = None
    institutional_net_5d_shares: int | None = None
    native_components: dict[str, int | None] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HotStock:
    """熱門/異動股(對齊 PanWatch src/collectors/discovery_collector.HotStock)。"""

    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None
    price_kind: str | None = None
    trade_date: str | None = None
    freshness: dict = field(default_factory=dict)


@dataclass(frozen=True)
class HotBoard:
    """熱門板塊(對齊 PanWatch src/collectors/discovery_collector.HotBoard)。"""

    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass
class EventItem:
    """結構化事件(對齊 PanWatch src/collectors/events_collector.EventItem)。"""

    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


@dataclass
class Fundamentals:
    """標準化基本面/財務資料(按 symbol)。估值類欄位/財報類欄位來源不同、可能分批到位,
    拿不到的欄位一律 None,不偽造。"""

    symbol: str
    market: str
    name: str = ""
    # —— 估值類 ——
    pe_ttm: float | None = None                    # 市盈率(TTM)
    pe_static: float | None = None                  # 市盈率(靜態)
    pb: float | None = None                         # 市淨率
    ps_ttm: float | None = None                     # 市銷率(TTM)
    total_market_value: float | None = None         # 總市值(億)
    circulating_market_value: float | None = None   # 流通市值(億)
    dividend_yield: float | None = None             # 股息率(%)
    total_shares: float | None = None               # 總股本(股)
    float_shares: float | None = None                # 流通股本(股)
    # —— 財報類 ——
    eps: float | None = None                        # 每股收益
    bps: float | None = None                        # 每股淨資產
    roe: float | None = None                        # 淨資產報酬率(%)
    revenue: float | None = None                    # 營業收入
    net_profit: float | None = None                 # 歸母淨利潤
    gross_margin: float | None = None               # 毛利率(%)
    net_margin: float | None = None                 # 淨利率(%)
    revenue_yoy: float | None = None                # 營收同比增長(%)
    net_profit_yoy: float | None = None             # 淨利潤同比增長(%)
    report_date: str = ""                           # 報告期(原樣字串,不做日期解析)
    timestamp: datetime = field(default_factory=datetime.now)
    # A provider's generic PE label is not necessarily trailing twelve months.
    pe_ratio: float | None = None
    valuation_trade_date: str | None = None
    valuation_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TwmdValuationObservation:
    """One official dated valuation row, preserving source decimal strings."""

    instrument_id: str
    symbol: str
    trade_date: str
    company_name: str
    close_price: str | None
    pe_ratio: str | None
    pb_ratio: str | None
    dividend_yield_pct: str | None
    dividend_per_share: str | None
    dividend_per_share_currency: str | None
    dividend_reference_year: int | None
    financial_reference_year: int | None
    financial_reference_quarter: int | None
    source_contract: str | None = None
    source_url: str | None = None
    request_scope: str | None = None
    received_at_utc: str | None = None
    payload_sha256: str | None = None
    capture_id: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class TwmdValuationRead:
    """A bounded official valuation read, including endpoint coverage evidence."""

    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdValuationObservation]
    status: str
    reason: str
    schema_ready: bool | None = None
    coverage_header: str | None = None
    selected_instrument_presence: str = "unknown"
    response_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InstitutionalFlowCoverage:
    """One source-native flow coverage entry for a requested calendar date."""

    trade_date: str
    status: str
    record_count: int
    selected_instrument_presence: str | None = None
    acquired_at: str | None = None
    received_at_utc: str | None = None
    sha256: str | None = None
    capture_id: str | None = None
    source_contract: str | None = None
    source_url: str | None = None
    request_scope: str | None = None
    payload_sha256: str | None = None


@dataclass(frozen=True)
class InstitutionalFlowObservation:
    """One official native-unit flow row with all source categories retained."""

    instrument_id: str
    symbol: str
    name: str
    trade_date: str
    native_unit: str
    native_values: dict[str, int | None]
    source_contract: str
    source_url: str | None = None
    request_scope: str | None = None
    acquired_at: str | None = None
    first_observed_at: str | None = None
    received_at_utc: str | None = None
    payload_sha256: str | None = None
    capture_id: str | None = None
    revision: int | None = None


@dataclass(frozen=True)
class InstitutionalFlowRead:
    """A bounded, typed institutional-flow response with native coverage."""

    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    source_contract: str
    native_unit: str
    schema_ready: bool
    coverage: list[InstitutionalFlowCoverage]
    data: list[InstitutionalFlowObservation]
    status: str
    reason: str
    request_scope: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class DragonTigerItem:
    """龍虎榜(東財每日龍虎榜明細,市場級,按 date 過濾)。欄位待實抓校準。"""

    trade_date: str
    symbol: str
    name: str = ""
    reason: str | None = None          # 上榜原因
    close: float | None = None         # 收盤價
    change_pct: float | None = None    # 漲跌幅(%)
    net_buy: float | None = None       # 龍虎榜淨買額(元)
    buy_amt: float | None = None       # 龍虎榜買入額(元)
    sell_amt: float | None = None      # 龍虎榜賣出額(元)
    turnover_pct: float | None = None  # 周轉率(%)


@dataclass
class MarginItem:
    """融資融券(東財 datacenter,按 symbol,取最新一條快照)。欄位待實抓校準。"""

    date: str
    symbol: str
    rz_balance: float | None = None     # 融資餘額(元)
    rz_buy: float | None = None         # 融資買入額(元)
    rz_repay: float | None = None       # 融資償還額(元)
    rq_balance: float | None = None     # 融券餘額(元)
    rq_sell_vol: float | None = None    # 融券賣出量(股)
    rq_repay_vol: float | None = None   # 融券償還量(股)
    total_balance: float | None = None  # 兩融餘額(元)
    quantity_unit: str | None = None
    margin_balance_lots: float | None = None
    margin_buy_lots: float | None = None
    margin_cash_repayment_lots: float | None = None
    short_balance_lots: float | None = None
    short_sell_lots: float | None = None
    short_repayment_lots: float | None = None


@dataclass
class ShareholderItem:
    """股東戶數(東財 datacenter,按 symbol,取最新一期)。欄位待實抓校準。"""

    report_date: str
    symbol: str
    holder_num: int | None = None      # 股東戶數
    change_num: int | None = None      # 戶數變化(較上期)
    change_ratio: float | None = None  # 戶數環比變化(%)
    avg_shares: float | None = None    # 戶均持股(股)


@dataclass
class DividendItem:
    """分紅(東財 datacenter,按 symbol,返回該只全部歷史)。欄位待實抓校準。"""

    ex_date: str
    symbol: str
    dividend_per_share: float | None = None  # 每股派息(稅前,元)
    transfer_ratio: float | None = None      # 每10股轉增(股)
    bonus_ratio: float | None = None         # 每10股送股(股)
    progress: str = ""                       # 方案進度


@dataclass
class NorthboundItem:
    """北向資金(同花順 hexin 當日分鐘累計淨買入,市場級,取當日末值快照)。
    欄位待實抓校準(沙箱代理攔截,無法驗證真實回應結構)。"""

    date: str
    hgt_net: float | None = None   # 滬股通淨買入(億元)
    sgt_net: float | None = None   # 深股通淨買入(億元)⚠️ 近期不可靠(可能 NaN/量級異常),需容錯
    total_net: float | None = None  # 北向合計=hgt_net+sgt_net;任一為 None 則 None(不臆造)
    time: str = ""                  # 末值對應的分鐘時間點(可選)


@dataclass
class FlashNews:
    """快訊(7×24,對齊 cls/sina/eastmoney 快訊流)。市場級,symbols 可空。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class NewsArticle:
    """新聞資訊(個股新聞+公告,對齊 PanWatch src/collectors/news_collector.NewsItem)。
    來源可為 xueqiu(雪球個股新聞)/ eastmoney_news(東財個股新聞搜尋)/ eastmoney(東財公告)。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class Response:
    """Engine 返回:承載 payload + 命中的 vendor/延遲。"""

    ok: bool
    data: Any = None
    error: str = ""
    vendor: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        if self.data is None:
            return True
        if isinstance(self.data, (list, tuple, dict, set)) and len(self.data) == 0:
            return True
        return False
