"""從環境和專案配置檔案讀取執行期設定的技術邊界。

該模組可同時被 HTTP、後臺任務和平臺介面卡使用；它不包含任何投資或產品決策。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings

from src.platform.marketdata.models import MarketCode


class Settings(BaseSettings):
    """環境變數配置"""

    # AI
    ai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    ai_api_key: str = ""
    ai_model: str = "glm-4"

    # Assistant context engineering. The compression model is optional: when
    # unset, the host reuses the configured default assistant model.
    context_compression_model_id: int | None = Field(default=None, ge=1)
    context_compression_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    context_summary_max_tokens: int = Field(default=800, ge=128, le=4_000)
    context_max_tokens: int = Field(default=12_000, ge=256)
    context_soft_limit_tokens: int = Field(default=8_400, ge=128)
    context_hard_limit_tokens: int = Field(default=10_200, ge=256)
    context_keep_recent_messages: int = Field(default=8, ge=1, le=100)
    tool_research_enabled: bool = True

    # Local PanWatch and twmd can run on separate ports.
    panwatch_port: int = Field(default=8000, ge=1, le=65535)
    playwright_chromium_executable: str = ""
    tw_data_provider: str = "twmd"
    tw_fundamentals_provider: Literal["twmd", "finmind"] = "twmd"
    tw_capital_flow_provider: Literal["twmd", "finmind"] = "twmd"
    tw_margin_provider: Literal["twmd", "finmind"] = "twmd"
    twmd_base_url: str = "http://127.0.0.1:8000"
    twmd_api_token: str = ""
    twmd_timeout_sec: float = Field(default=5, gt=0)
    # The profile projection can take over 15 seconds on the local query API.
    # Keep its bounded read timeout independent from the normal market-data SLA.
    twmd_profile_timeout_sec: float = Field(default=20, gt=0)
    twmd_control_base_url: str = "http://127.0.0.1:9200"
    twmd_control_agent_token: str = ""
    finmind_api_token: str = ""
    external_quote_feed_url: str = Field(default="http://127.0.0.1:8088", validation_alias=AliasChoices(
        "EXTERNAL_QUOTE_FEED_URL", "TW_QUOTE_FEED_URL"))
    external_quote_feed_token: str = Field(default="", validation_alias=AliasChoices(
        "EXTERNAL_QUOTE_FEED_TOKEN", "TW_QUOTE_FEED_TOKEN"))
    external_quote_feed_timeout_sec: str = Field(default="5", validation_alias=AliasChoices(
        "EXTERNAL_QUOTE_FEED_TIMEOUT_SEC", "TW_QUOTE_FEED_TIMEOUT_SEC"))
    tw_paper_lot_size: int = 1000
    tw_commission_rate: float = Field(default=0.001425, ge=0)
    tw_min_commission: float = Field(default=20, ge=0)
    tw_extra_closed_dates: str = ""

    # Telegram
    notify_telegram_bot_token: str = ""
    notify_telegram_chat_id: str = ""

    # 代理
    http_proxy: str = ""

    # 通知策略（可透過 UI 的“系統設定”覆蓋）
    # 靜默時間段（本地時區），格式: HH:MM-HH:MM，空為關閉；跨夜示例: 23:00-07:00
    notify_quiet_hours: str = ""
    # 通知失敗重試次數（不含首次嘗試）
    notify_retry_attempts: int = 2
    # 重試退避秒數（基數），實際會按 1x,2x,... 遞增
    notify_retry_backoff_seconds: float = 2.0
    # 冪等視窗覆蓋（JSON），示例: {"news_digest":60,"daily_report":720}
    notify_dedupe_ttl_overrides: str = ""

    # SSL 證書（企業環境）
    ca_cert_file: str = ""

    # 排程
    # day_of_week 使用 POSIX cron 語義(1-5=週一到週五)
    daily_report_cron: str = "30 15 * * 1-5"

    # 預設時區（用於排程、時間展示等）。
    # 統一使用一個環境變數控制：TZ（預設 Asia/Shanghai）。
    # 建議使用 IANA 時區名，如 Asia/Shanghai, America/New_York。
    app_timezone: str = Field(
        default="Asia/Shanghai",
        validation_alias=AliasChoices("TZ", "APP_TIMEZONE"),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # .env 裡可能有 HTTPS_PROXY 等未宣告欄位(httpx/系統標準變數),忽略不報錯
        "extra": "ignore",
    }

    @model_validator(mode="after")
    def validate_context_thresholds(self) -> "Settings":
        if not self.context_soft_limit_tokens < self.context_hard_limit_tokens <= self.context_max_tokens:
            raise ValueError(
                "context thresholds must satisfy soft_limit < hard_limit <= max_tokens"
            )
        return self


@dataclass
class StockConfig:
    """自選股配置"""

    symbol: str
    name: str
    market: MarketCode


@dataclass
class AppConfig:
    """應用完整配置"""

    settings: Settings
    watchlist: list[StockConfig] = field(default_factory=list)


def load_watchlist(path: str | Path = "config/watchlist.yaml") -> list[StockConfig]:
    """從 YAML 載入自選股列表"""
    path = Path(path)
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    stocks = []
    for market_group in data.get("markets", []):
        market_code = MarketCode(market_group["code"])
        for stock in market_group.get("stocks", []):
            stocks.append(
                StockConfig(
                    symbol=stock["symbol"],
                    name=stock["name"],
                    market=market_code,
                )
            )

    return stocks


def load_config() -> AppConfig:
    """載入完整配置"""
    settings = Settings()
    watchlist = load_watchlist()
    return AppConfig(settings=settings, watchlist=watchlist)
