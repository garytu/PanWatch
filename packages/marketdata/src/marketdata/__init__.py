"""marketdata —— 多市場行情資料抓取層(可插拔資料來源)。"""

from marketdata.client import MarketData
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.errors import MarketDataError, TwmdReadError, VendorError
from marketdata.http import capture_errors, record_error
from marketdata.ports import ConfigProvider, MetricsSink, SourceConfig
from marketdata.registry import PACKAGE_VENDORS_BY_TYPE
from marketdata.symbol import Market, Symbol
from marketdata.types import (
    Bar,
    CapitalFlow,
    DividendItem,
    DragonTigerItem,
    EventItem,
    FlashNews,
    Fundamentals,
    HotBoard,
    HotStock,
    InstitutionalFlowCoverage,
    InstitutionalFlowObservation,
    InstitutionalFlowRead,
    MarginItem,
    NewsArticle,
    NorthboundItem,
    Quote,
    Request,
    Response,
    ShareholderItem,
    TwmdCompanyProfile,
    TwmdCompanyProfileRead,
    TwmdCompanyProfileSnapshot,
    TwmdBrokerFlowCoverageObservation,
    TwmdBrokerFlowCoverageRead,
    TwmdBrokerFlowPriceLevelObservation,
    TwmdBrokerFlowPriceLevelsRead,
    TwmdBrokerFlowQuantityObservation,
    TwmdBrokerFlowQuantityRead,
    TwmdMonthlyRevenueCoverage,
    TwmdMonthlyRevenueMonth,
    TwmdMonthlyRevenueRead,
    TwmdMonthlyRevenueRow,
    TwmdCoverageObservation,
    TwmdMarginShortSaleObservation,
    TwmdMarginShortSaleRead,
    TwmdShareholderDistributionObservation,
    TwmdShareholderDistributionRead,
    TwmdValuationObservation,
    TwmdValuationRead,
)

__version__ = "0.1.0"

__all__ = [
    "MarketData", "Symbol", "Market", "Bar", "CapitalFlow", "EventItem", "FlashNews", "Fundamentals",
    "InstitutionalFlowCoverage", "InstitutionalFlowObservation", "InstitutionalFlowRead",
    "TwmdCompanyProfile", "TwmdCompanyProfileSnapshot", "TwmdCompanyProfileRead",
    "TwmdBrokerFlowQuantityObservation", "TwmdBrokerFlowQuantityRead",
    "TwmdBrokerFlowCoverageObservation", "TwmdBrokerFlowCoverageRead",
    "TwmdBrokerFlowPriceLevelObservation", "TwmdBrokerFlowPriceLevelsRead",
    "TwmdMonthlyRevenueRow", "TwmdMonthlyRevenueMonth", "TwmdMonthlyRevenueCoverage",
    "TwmdMonthlyRevenueRead",
    "TwmdCoverageObservation", "TwmdMarginShortSaleObservation", "TwmdMarginShortSaleRead",
    "TwmdShareholderDistributionObservation", "TwmdShareholderDistributionRead",
    "TwmdValuationObservation", "TwmdValuationRead",
    "HotStock", "HotBoard", "NewsArticle",
    "DragonTigerItem", "MarginItem", "ShareholderItem", "DividendItem", "NorthboundItem",
    "Quote", "Request", "Response",
    "SourceConfig", "ConfigProvider", "MetricsSink",
    "StaticConfigProvider", "InMemoryMetricsSink",
    "PACKAGE_VENDORS_BY_TYPE",
    "capture_errors", "record_error",
    "MarketDataError", "VendorError", "TwmdReadError", "__version__",
]
