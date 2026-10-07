"""請求 / 回應 / 行情資料型別。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
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
class TaiwanDiscoveryPool:
    """Bounded TW cash-instrument price pool used by official-factor discovery."""

    items: list[HotStock]
    status: str
    catalog_count: int
    eligible_catalog_count: int
    scanned_instrument_count: int
    price_snapshot_count: int
    catalog_request_count: int
    price_snapshot_request_count: int
    price_universe_selected_count: int = 0
    price_snapshot_batches_planned: int = 0
    unattempted_price_snapshot_batches: int = 0
    price_snapshot_batches_pending_count: int = 0
    ranked_price_count: int = 0
    failed_request_count: int = 0
    cache_hits: int = 0
    partial_scan: bool = False
    error_reason: str | None = None
    excluded_security_type_counts: dict[str, int] = field(default_factory=dict)
    security_type_by_instrument_id: dict[str, str] = field(default_factory=dict)
    price_data_dates: list[str] = field(default_factory=list)
    provider_scope: str = ""


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
class TwmdMaterialInformationEvent:
    """One source-family-local announcement, preserving publisher text verbatim."""

    instrument_id: str
    symbol: str
    announcement_date: str
    announced_at: str
    company_name: str
    subject: str
    clause: str
    fact_date: str
    detail: str
    content_hash: str
    revision: int
    first_observed_at_utc: str
    event_first_observed_at_utc: str
    latest_observed_at_utc: str
    capture_id: str
    acquisition_date: str
    payload_sha256: str
    source_event_id: str | None = None
    provider_key: str | None = None
    report_date: str | None = None
    row_fingerprint: str | None = None
    detail_subject_raw: str | None = None
    speaker_name: str | None = None
    speaker_title: str | None = None
    speaker_phone: str | None = None
    revision_count: int | None = None
    list_payload_sha256: str | None = None
    source_generated_at: str | None = None


@dataclass(frozen=True)
class TwmdMaterialInformationCapture:
    """Original capture/acquisition evidence returned by the selected source."""

    capture_id: str
    received_at_utc: str
    payload_sha256: str
    byte_length: int
    report_date: str | None = None
    acquisition_date: str | None = None
    row_count: int | None = None
    instrument_id: str | None = None
    query_year: int | None = None
    response_class: str | None = None
    coverage_through: str | None = None
    source_generated_at: str | None = None
    event_count: int | None = None
    details_complete: bool | None = None


@dataclass(frozen=True)
class TwmdMaterialInformationRead:
    """Bounded current or historical TWSE issuer-announcement read."""

    instrument_id: str
    dataset: str
    source_contract: str
    source_family: str
    unsupported_reason: str | None
    schema_ready: bool
    coverage_status: str
    history_complete: bool
    history_note: str
    partial_current_day: bool
    start_date: str
    end_date: str
    limit: int
    retained_count: int
    returned_count: int
    truncated: bool
    latest_capture: TwmdMaterialInformationCapture | None
    acquisitions: list[TwmdMaterialInformationCapture]
    missing_dates: list[str]
    data: list[TwmdMaterialInformationEvent]
    response_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TwmdExRightDividendObservation:
    """One TWSE realized TWT49U result; decimals retain response lexical form."""

    effective_date: str
    instrument_id: str
    symbol: str
    observed_name: str
    action_kind: str
    prior_close: str
    reference_price: str
    rights_dividend_value: str
    limit_up_price: str
    limit_down_price: str
    opening_auction_basis: str
    dividend_adjusted_reference_price: str
    provider: str
    currency: str


@dataclass(frozen=True)
class TwmdExRightDividendRead:
    """Bounded TWT49U read. Empty data has unknown, not empty, coverage."""

    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdExRightDividendObservation]
    status: str
    reason: str
    dataset_coverage: str = "unknown"


@dataclass(frozen=True)
class TwmdCapitalReductionObservation:
    """One TWSE realized TWTAUU recovery result with exact decimal strings."""

    recovery_date: str
    instrument_id: str
    symbol: str
    observed_name: str
    reduction_reason: str
    pre_suspension_close: str
    recovery_reference_price: str
    limit_up_price: str
    limit_down_price: str
    opening_auction_basis: str
    ex_right_reference_price: str | None
    provider: str
    currency: str


@dataclass(frozen=True)
class TwmdCapitalReductionRead:
    """Bounded TWTAUU read. Empty data has unknown, not empty, coverage."""

    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdCapitalReductionObservation]
    status: str
    reason: str
    dataset_coverage: str = "unknown"


@dataclass(frozen=True)
class TwmdBenchmarkDefinition:
    """One official index benchmark identity, separate from stock instruments."""

    benchmark_id: str
    venue: str
    name: str
    name_zh: str
    provider: str
    source_alias: str
    source_contract: str
    source_url: str
    price_kind: str = "benchmark_index"
    unit: str = "index_points"
    basis: str = "raw_price_index"
    stock_venue_default: str = ""


@dataclass(frozen=True)
class TwmdBenchmarkBar:
    """One official publisher-dated benchmark bar with its winning capture."""

    benchmark_id: str
    trade_date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    unit: str
    basis: str
    revision: int
    capture_id: str
    captured_at: str
    source_contract: str
    source_alias: str
    source_url: str
    request_scope: str
    payload_sha256: str


@dataclass(frozen=True)
class TwmdBenchmarkProvenance:
    """One bounded capture receipt in a benchmark query response."""

    capture_id: str
    benchmark_id: str
    captured_at: str
    acquisition_date: str
    source_contract: str
    source_alias: str
    source_url: str
    request_scope: str
    publication_start: str | None
    publication_end: str | None
    payload_sha256: str
    record_count: int


@dataclass(frozen=True)
class TwmdBenchmarkCoverageDay:
    trade_date: str
    status: str


@dataclass(frozen=True)
class TwmdBenchmarkBarsRead:
    """A validated, bounded official benchmark bar read and source evidence."""

    benchmark_id: str
    venue: str
    provider: str
    source_alias: str
    source_contract: str
    source_url: str
    timeframe: str
    price_kind: str
    adjustment_mode: str
    unit: str
    basis: str
    limit: int
    returned_count: int
    total_count: int
    partial: bool
    truncated: bool
    requested_start_date: str | None
    requested_end_date: str | None
    coverage_window: dict[str, object]
    coverage: tuple[TwmdBenchmarkCoverageDay, ...]
    gaps: tuple[str, ...]
    bars: tuple[TwmdBenchmarkBar, ...]
    provenance: tuple[TwmdBenchmarkProvenance, ...]
    provenance_total_count: int
    provenance_truncated: bool
    served_at: str


@dataclass(frozen=True)
class TwmdDailyPriceBar:
    """A raw TWSE/TPEx daily close from the upstream latest-only bars API."""

    trade_date: str
    close: Decimal | None
    observation_status: str
    coverage_status: str
    coverage_dataset: str | None = None
    coverage_record_count: int | None = None
    coverage_acquired_at: str | None = None
    coverage_checksum: str | None = None


@dataclass(frozen=True)
class TwmdDailyBarsRead:
    """Bounded latest-N raw stock bars, before any requested-range filtering."""

    instrument_id: str
    symbol: str
    venue: str
    name: str
    timeframe: str
    price_kind: str
    adjustment_mode: str
    provider: str
    limit: int
    returned_count: int
    partial: bool
    bars: tuple[TwmdDailyPriceBar, ...]
    latest_dataset_trade_date: str | None


@dataclass(frozen=True)
class TwmdFinancialStatementDimension:
    axis_qname: str
    member_qname: str


@dataclass(frozen=True)
class TwmdFinancialStatementPeriod:
    kind: str
    instant: str | None
    start_date: str | None
    end_date: str | None


@dataclass(frozen=True)
class TwmdFinancialStatementContext:
    source_id: str
    entity_identifier: str
    entity_scheme: str
    period: TwmdFinancialStatementPeriod
    dimensions: tuple[TwmdFinancialStatementDimension, ...]


@dataclass(frozen=True)
class TwmdFinancialStatementUnit:
    source_id: str
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]


@dataclass(frozen=True)
class TwmdFinancialStatementFact:
    statement: str
    occurrence_ordinal: int
    concept_qname: str
    context: TwmdFinancialStatementContext
    unit: TwmdFinancialStatementUnit
    value: str | None
    is_nil: bool
    lexical_value: str
    format_qname: str | None
    scale: int | None
    sign: str | None
    decimals: str | None
    precision: str | None


@dataclass(frozen=True)
class TwmdFinancialStatementQualification:
    status: str
    reason: str
    industry_code: str | None
    catalog_evidence: dict[str, object] | None
    profile_evidence: dict[str, object] | None


@dataclass(frozen=True)
class TwmdFinancialStatementCoverage:
    status: str
    reason: str
    latest_discovery_presence: str
    capture_id: str | None
    original_received_at_utc: str | None


@dataclass(frozen=True)
class TwmdFinancialStatementReport:
    document_id: str
    capture_id: str
    semantic_revision_id: str
    member_filename: str
    source_url: str
    raw_sha256: str
    source_contract: str
    parser_contract: str
    original_received_at_utc: str
    document_first_observed_at_utc: str
    semantic_revision_first_observed_at_utc: str
    latest_observed_at_utc: str
    published_at_utc: str | None
    amendment_status: str


@dataclass(frozen=True)
class TwmdFinancialStatementRead:
    instrument_id: str
    fiscal_year: int
    fiscal_quarter: int
    report_scope: str
    statement: str | None
    limit: int
    qualification: TwmdFinancialStatementQualification
    coverage: TwmdFinancialStatementCoverage
    report: TwmdFinancialStatementReport | None
    facts: tuple[TwmdFinancialStatementFact, ...]
    total_fact_count: int
    returned_fact_count: int
    truncated: bool
    status: str
    reason: str
    endpoint: str = "/api/v1/financial-statements"


@dataclass(frozen=True)
class TwmdCompanyProfileSnapshot:
    """Latest whole-market profile snapshot evidence from twmd."""

    capture_id: str
    report_date: str
    received_at_utc: str
    source_contract: str
    payload_sha256: str
    row_count: int
    coverage_status: str


@dataclass(frozen=True)
class TwmdCompanyProfile:
    """One retained latest issuer profile with publisher and catalog evidence."""

    instrument_id: str
    company_name: str
    industry_code: str
    established_on: str
    listed_on: str
    par_value_raw: str
    par_value_amount: str | None
    par_value_currency: str | None
    par_value_meaning: str
    paid_in_capital: str
    issued_share_count: int
    private_share_count: int | None
    preferred_share_count: int | None
    financial_report_type_code: str
    source_content_hash: str
    report_date: str
    original_received_at_utc: str
    source_contract: str
    payload_sha256: str
    revision: int
    share_semantics: str
    qualification: str
    qualification_reason: str
    latest_snapshot_report_date: str | None
    latest_snapshot_received_at_utc: str | None
    latest_snapshot_presence: str
    snapshot_capture_id: str | None


@dataclass(frozen=True)
class TwmdCompanyProfileRead:
    """Latest-only issuer profile result and independent snapshot coverage."""

    instrument_id: str
    endpoint: str
    source_contract: str
    schema_ready: bool
    coverage_status: str
    latest_snapshot_presence: str
    qualification: str
    qualification_reason: str
    units: dict[str, str]
    latest_snapshot: TwmdCompanyProfileSnapshot | None
    profile: TwmdCompanyProfile | None
    status: str
    reason: str


@dataclass(frozen=True)
class TwmdMonthlyRevenueRow:
    """One exact publisher monthly-revenue observation and its provenance."""

    symbol: str
    data_month: str
    company_name: str
    industry: str
    monthly_revenue: str | None
    previous_month_revenue: str | None
    year_ago_monthly_revenue: str | None
    month_over_month_pct: str | None
    year_over_year_pct: str | None
    cumulative_revenue: str | None
    year_ago_cumulative_revenue: str | None
    cumulative_yoy_pct: str | None
    notes: str
    content_hash: str
    revision: int
    capture_id: str
    source: str
    source_contract: str
    request_scope: str
    source_url: str
    acquisition_date: str
    received_at_utc: str
    report_date: str
    payload_sha256: str


@dataclass(frozen=True)
class TwmdMonthlyRevenueMonth:
    """Per-month selected-issuer presence plus any retained issuer row."""

    data_month: str
    presence: str
    row: TwmdMonthlyRevenueRow | None


@dataclass(frozen=True)
class TwmdMonthlyRevenueCoverage:
    """One source-native report entry for one actual data month."""

    capture_id: str
    source: str
    data_month: str
    row_count: int
    source_contract: str
    request_scope: str
    source_url: str
    acquisition_date: str
    received_at_utc: str
    report_date: str
    payload_sha256: str
    selected_issuer_present: bool


@dataclass(frozen=True)
class TwmdMonthlyRevenueRead:
    """Bounded monthly-revenue series, including month-by-month evidence."""

    instrument_id: str
    endpoint: str
    dataset: str
    start_month: str
    end_month: str
    schema_ready: bool
    coverage_status: str
    qualification: str
    qualification_reason: str
    current_catalog_evidence: dict[str, object]
    units: dict[str, str]
    coverage: list[TwmdMonthlyRevenueCoverage]
    months: list[TwmdMonthlyRevenueMonth]
    served_at: str
    status: str
    reason: str


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


@dataclass(frozen=True)
class TwmdMarginShortSaleObservation:
    """One official TWMD margin observation; every quantity is a trading unit."""

    instrument_id: str
    symbol: str
    trade_date: str
    margin_balance_previous: int
    margin_purchase: int
    margin_sale: int
    margin_cash_redemption: int
    margin_balance: int
    margin_securities_finance_balance: int | None
    margin_utilization_rate: Decimal | None
    margin_quota: int
    short_sale_balance_previous: int
    short_sale: int
    short_cover: int
    short_stock_redemption: int
    short_sale_balance: int
    short_sale_securities_finance_balance: int | None
    short_sale_utilization_rate: Decimal | None
    short_sale_quota: int
    offsetting: int
    note: str | None
    native_unit: str = "trading_units"


@dataclass(frozen=True)
class TwmdCoverageObservation:
    dataset: str
    partition_key: str
    status: str
    record_count: int
    acquired_at: str | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class TwmdMarginShortSaleRead:
    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdMarginShortSaleObservation]
    coverage: list[TwmdCoverageObservation]
    status: str
    reason: str
    coverage_error_reason: str | None = None


@dataclass(frozen=True)
class TwmdShareholderDistributionObservation:
    """TDCC custody-account bucket. Accounts are not investor identities."""

    report_date: str
    instrument_id: str
    symbol: str
    report_variant: str
    row_kind: str
    source_level: int
    source_tier_label: str | None
    holder_count: int
    share_count: int
    share_percentage_points: Decimal
    provider: str
    native_unit: str


@dataclass(frozen=True)
class TwmdShareholderDistributionRead:
    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    report_variant: str | None
    data: list[TwmdShareholderDistributionObservation]
    coverage: list[TwmdCoverageObservation]
    status: str
    reason: str
    coverage_error_reasons: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TwmdBrokerFlowQuantityObservation:
    """One source-preserving Capital-lot or TWSE-share branch observation."""

    provider: str
    dataset: str
    instrument_id: str
    symbol: str
    trade_date: str
    source_branch_key: str
    branch_code: str
    branch_name: str
    native_unit: str
    precision_shares: int
    buy_native: int
    sell_native: int
    net_native: int
    buy_vwap: Decimal | None = None
    sell_vwap: Decimal | None = None
    revision_id: str | None = None


@dataclass(frozen=True)
class TwmdBrokerFlowCoverageObservation:
    """One canonical daily coverage outcome; statuses remain source-native."""

    provider: str
    dataset: str
    instrument_id: str
    trade_date: str
    status: str
    record_count: int
    revision_id: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class TwmdBrokerFlowPriceLevelObservation:
    """One exact TWSE execution-price row for a materialized detail date."""

    provider: str
    dataset: str
    instrument_id: str
    symbol: str
    trade_date: str
    source_branch_key: str
    branch_code: str
    branch_name: str
    price: Decimal
    buy_native: int
    sell_native: int
    native_unit: str
    precision_shares: int
    revision_id: str


@dataclass(frozen=True)
class TwmdBrokerFlowQuantityRead:
    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdBrokerFlowQuantityObservation]
    status: str
    reason: str


@dataclass(frozen=True)
class TwmdBrokerFlowCoverageRead:
    instrument_id: str
    endpoint: str
    start_date: str
    end_date: str
    data: list[TwmdBrokerFlowCoverageObservation]
    status: str
    reason: str


@dataclass(frozen=True)
class TwmdBrokerFlowPriceLevelsRead:
    instrument_id: str
    endpoint: str
    trade_date: str
    data: list[TwmdBrokerFlowPriceLevelObservation]
    status: str
    reason: str


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
    """Legacy margin snapshot; native quantity fields can be lots, trading units, or shares by provider."""

    date: str
    symbol: str
    rz_balance: float | None = None     # 融資餘額(元)
    rz_buy: float | None = None         # 融資買入額(元)
    rz_repay: float | None = None       # 融資償還額(元)
    rq_balance: float | None = None     # 融券餘額(元)
    rq_sell_vol: float | None = None    # 融券賣出量(股)
    rq_repay_vol: float | None = None   # 融券償還量(股)
    total_balance: float | None = None  # 兩融餘額(元)
    quantity_unit: str | None = None   # Describes the margin/short balance quantities below.
    margin_balance_lots: float | None = None
    margin_buy_lots: float | None = None
    margin_cash_repayment_lots: float | None = None
    short_balance_lots: float | None = None
    short_sell_lots: float | None = None
    short_repayment_lots: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


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
