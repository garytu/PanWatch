"""PanWatch 統一服務入口 - Web 後臺 + Agent 排程"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn

from src.platform.persistence.database import init_db, SessionLocal
from src.platform.persistence.models import (
    AgentConfig,
    Stock,
    StockAgent,
    AIService,
    AIModel,
    NotifyChannel,
    AppSettings,
    DataSource,
)
from src.platform.observability.log_handler import DBLogHandler
from src.platform.runtime.config import Settings, AppConfig, StockConfig
from src.platform.marketdata.models import MarketCode
from src.platform.ai.ai_client import AIClient
from src.platform.ai.ai_failover import build_failover_client
from src.platform.notifications.notifier import NotifierManager
from src.modules.automation.agent_scheduler import AgentScheduler
from src.modules.market.price_alert_scheduler import PriceAlertScheduler
from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler
from src.modules.research.context_scheduler import ContextMaintenanceScheduler
from src.modules.automation.agent_runs import record_agent_run
from src.platform.observability.log_context import install_log_record_factory, log_context
from src.modules.automation.agent_catalog import (
    AGENT_SEED_SPECS,
    AGENT_KIND_WORKFLOW,
)
from src.modules.strategy.strategy_catalog import ensure_strategy_catalog
from src.modules.automation.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.news_digest import NewsDigestAgent
from src.modules.automation.chart_analyst import ChartAnalystAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.automation.premarket_outlook import PremarketOutlookAgent
from src.modules.automation.tradingagents import TradingAgentsAgent
from src.modules.market.data_collector import DEFAULT_TEST_SYMBOLS

logger = logging.getLogger(__name__)

# 全域性 scheduler 例項，供 agents API 呼叫
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None
paper_trading_scheduler: PaperTradingScheduler | None = None
context_maintenance_scheduler: ContextMaintenanceScheduler | None = None


def apply_proxy_env(proxy: str | None) -> None:
    """統一更新程式環境變數代理,讓所有 httpx 預設 Client (trust_env=True) 走該代理。

    傳空字串 / None 時清除環境變數(取消代理)。
    NO_PROXY 預設含 localhost / 迴環地址,避免本地訪問繞一圈。
    """
    p = (proxy or "").strip()
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        logger.info(f"HTTP/HTTPS 代理已應用: {p}")
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        logger.info("HTTP/HTTPS 代理已清除")


def setup_proxy():
    """啟動時把已配置的 HTTP 代理橋接到環境變數。

    優先順序:
    1. 已存在的 HTTP_PROXY / HTTPS_PROXY 環境變數(使用者顯式覆蓋,不動)
    2. app_settings.http_proxy(UI 配置)
    3. .env 中的 http_proxy(Settings.http_proxy)
    """
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        logger.info(
            f"沿用現有環境變數代理: HTTP_PROXY={os.environ.get('HTTP_PROXY', '')} "
            f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY', '')}"
        )
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        return

    proxy = ""
    try:
        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            if setting and setting.value:
                proxy = setting.value.strip()
        finally:
            db.close()
    except Exception:
        pass

    if not proxy:
        proxy = (Settings().http_proxy or "").strip()

    if proxy:
        apply_proxy_env(proxy)


def setup_ssl():
    """設定 SSL 證書環境（企業代理環境）"""
    settings = Settings()
    ca_cert = settings.ca_cert_file
    if not ca_cert or not os.path.exists(ca_cert):
        return

    import certifi

    bundle_path = os.path.join(os.path.dirname(__file__), "data", "ca-bundle.pem")
    os.makedirs(os.path.dirname(bundle_path), exist_ok=True)

    need_rebuild = not os.path.exists(bundle_path) or os.path.getmtime(
        ca_cert
    ) > os.path.getmtime(bundle_path)

    if need_rebuild:
        with open(bundle_path, "w") as out:
            with open(certifi.where(), "r") as f:
                out.write(f.read())
            out.write("\n")
            with open(ca_cert, "r") as f:
                out.write(f.read())

    os.environ["SSL_CERT_FILE"] = bundle_path
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_path
    logger.info(f"SSL 證書已載入: {bundle_path}")


def setup_logging():
    """配置日誌: 主控台 + 資料庫

    分級策略:
    - root logger 始終 DEBUG,所有日誌都會傳播到 handler
    - 主控台 handler 按 LOG_LEVEL 過濾(預設 INFO),並丟棄 httpx 等三方庫的 < WARNING 噪音
    - DB handler 始終 DEBUG 全量收錄,UI 日誌板永遠可以看到包括心跳/httpx 請求在內的完整記錄
    """
    console_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    console_level = getattr(logging, console_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    install_log_record_factory()

    # reload/server restart 時避免重複 handler 導致日誌放大。
    for h in list(root.handlers):
        if isinstance(h, DBLogHandler) or getattr(h, "_panwatch_console", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # 主控台輸出: 按 LOG_LEVEL 過濾,且丟棄三方庫的低階別噪音
    console = logging.StreamHandler()
    console._panwatch_console = True  # type: ignore[attr-defined]
    console.setLevel(console_level)
    console.addFilter(_ConsoleNoiseFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # 資料庫持久化: 始終全量收錄,UI 日誌板可查 DEBUG
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)

    # uvicorn 預設給自己掛了 stderr handler 並且 propagate=False,導致 access log
    # 走自己的鏈路(`INFO: 127.0.0.1 - "GET /api/..."`)不被我們的 filter 攔截。
    # 改成清空自己的 handler + propagate 到 root,讓 _ConsoleNoiseFilter 生效。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)


class _ConsoleNoiseFilter(logging.Filter):
    """主控台 handler 過濾器: 三方庫的 INFO/DEBUG 不進 stdout,WARNING+ 仍然顯示。
    DB handler 不掛這個過濾器,UI 日誌板能看到完整請求記錄。

    uvicorn.access 是每條請求的 access log(`INFO: 127.0.0.1 - "GET /api/..." 200 OK`),
    屬於底層心跳;uvicorn / uvicorn.error 是應用級日誌(啟動、報錯),保留。"""

    _NOISY_PREFIXES = ("httpx", "httpcore", "urllib3", "apscheduler", "uvicorn.access")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        name = record.name or ""
        for prefix in self._NOISY_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                return False
        return True


def setup_playwright():
    """檢查並安裝 Playwright 瀏覽器

    本地開發時使用系統安裝的 Playwright，Docker 環境下安裝到 data 目錄。
    透過 DOCKER 環境變數或顯式設定的 PLAYWRIGHT_BROWSERS_PATH 來判斷。
    """
    import subprocess

    # 允許透過環境變數跳過首次安裝（例如不需要截圖功能時）
    if os.environ.get("PLAYWRIGHT_SKIP_BROWSER_INSTALL") == "1":
        logger.info(
            "已設定 PLAYWRIGHT_SKIP_BROWSER_INSTALL=1，跳過 Playwright 瀏覽器安裝"
        )
        return

    # 如果使用者已顯式設定 PLAYWRIGHT_BROWSERS_PATH，尊重該設定
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        browser_dir = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        logger.info(f"使用自定義 Playwright 路徑: {browser_dir}")
    # Docker 環境下安裝到 data 目錄
    elif os.environ.get("DOCKER") == "1":
        data_dir = os.environ.get("DATA_DIR", "./data")
        browser_dir = os.path.join(data_dir, "playwright")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_dir
        logger.info(f"Docker 環境，Playwright 路徑: {browser_dir}")
    else:
        # 本地開發，使用系統預設路徑，不做任何安裝
        logger.info("本地開發環境，使用系統 Playwright")
        return

    # 檢查是否已安裝
    if os.path.exists(browser_dir):
        try:
            dirs = os.listdir(browser_dir)
            if any(
                d.startswith("chromium")
                for d in dirs
                if os.path.isdir(os.path.join(browser_dir, d))
            ):
                logger.info(f"Playwright 瀏覽器已就緒: {browser_dir}")
                return
        except Exception:
            pass

    # 首次安裝
    logger.info("首次啟動，正在安裝 Playwright 瀏覽器（可能需要幾分鐘）...")
    os.makedirs(browser_dir, exist_ok=True)

    try:
        result = subprocess.run(
            ["playwright", "install", "chromium"],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browser_dir},
            capture_output=True,
            text=True,
            timeout=600,  # 10 分鐘超時
        )
        if result.returncode == 0:
            logger.info("Playwright 瀏覽器安裝完成")
        else:
            logger.error(f"Playwright 安裝失敗: {result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Playwright 安裝超時（網路問題？）")
    except FileNotFoundError:
        logger.warning("Playwright 命令不可用，K線截圖功能不可用")
    except Exception as e:
        logger.error(f"Playwright 安裝失敗: {e}")


def seed_sample_stocks():
    """首次啟動時新增示例股票"""
    db = SessionLocal()
    try:
        # 只在沒有任何股票時才新增示例
        if db.query(Stock).count() > 0:
            return

        samples = [
            {"symbol": "600519", "name": "貴州茅臺", "market": "CN"},
            {"symbol": "002594", "name": "比亞迪", "market": "CN"},
            {"symbol": "300750", "name": "寧德時代", "market": "CN"},
            {"symbol": "00700", "name": "騰訊控股", "market": "HK"},
            {"symbol": "AAPL", "name": "蘋果", "market": "US"},
        ]
        for s in samples:
            db.add(Stock(**s))
        db.commit()
        logger.info("已新增 5 只示例股票（首次啟動）")
    finally:
        db.close()


def seed_agents():
    """初始化內建 Agent 配置"""
    db = SessionLocal()
    for spec in AGENT_SEED_SPECS:
        existing = db.query(AgentConfig).filter(AgentConfig.name == spec.name).first()
        if not existing:
            db.add(
                AgentConfig(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    kind=spec.kind,
                    visible=spec.visible,
                    lifecycle_status=spec.lifecycle_status,
                    replaced_by=spec.replaced_by,
                    display_order=spec.display_order,
                    enabled=spec.enabled,
                    schedule=spec.schedule,
                    execution_mode=spec.execution_mode,
                    config=spec.config or {},
                )
            )
        else:
            # 始終同步 execution_mode（確保程式碼中的定義生效）
            existing.execution_mode = spec.execution_mode or "batch"
            # 同步 display_name 和 description
            existing.display_name = spec.display_name or existing.display_name
            existing.description = spec.description or existing.description
            existing.kind = spec.kind
            existing.visible = bool(spec.visible)
            existing.lifecycle_status = spec.lifecycle_status or "active"
            existing.replaced_by = spec.replaced_by or ""
            existing.display_order = int(spec.display_order or 0)

            # capability 強制不參與排程，避免舊配置繼續觸發。
            if spec.kind != AGENT_KIND_WORKFLOW:
                existing.enabled = False
                existing.schedule = ""

            # 僅在使用者未配置時補齊預設 config
            if spec.config and (not existing.config):
                existing.config = spec.config
            # 對已存在配置做“向前相容”的欄位補齊（不覆蓋使用者已有值）
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


# 預置資料來源種子(供 seed_data_sources / reconcile_data_sources 複用)。
# 只增不刪的 upsert 目標;刪孤兒的對帳邏輯見 reconcile_data_sources。
DATA_SOURCE_SEEDS: list[dict] = [
        # 新聞類資料來源
        {
            "name": "雪球資訊",
            "type": "news",
            "provider": "xueqiu",
            "config": {
                "cookies": "",
                "description": "雪球個股新聞聚合，需要登入 cookie",
            },
            "enabled": False,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富資訊",
            "type": "news",
            "provider": "eastmoney_news",
            "config": {},
            "enabled": True,
            "priority": 1,
            "supports_batch": False,  # 每隻股票單獨請求
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富公告",
            "type": "news",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 2,
            "supports_batch": True,  # 支援批次查詢
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # K線資料來源
        {
            "name": "騰訊K線",
            "type": "kline",
            "provider": "tencent",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富 K線",
            "type": "kline",
            "provider": "eastmoney",
            "config": {"description": "東方財富日線,A股/港股長曆史兜底(免 key)。"},
            "enabled": True,
            "priority": 5,   # 騰訊(0)之後、Tushare(10)之前 → CN/HK 兜底
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "Stooq K線",
            "type": "kline",
            "provider": "stooq",
            "config": {"description": "Stooq 美股日線兜底(免 key)。"},
            "enabled": True,
            "priority": 15,  # US 兜底(騰訊 0 之後)
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "Yahoo K線",
            "type": "kline",
            "provider": "yahoo",
            "config": {
                "description": "Yahoo chart v8 日線(US/HK,免 key 免 crumb)。國內訪問通常需代理,"
                "在 config.proxy 填寫代理地址後啟用,作港股 K線第二源/美股更穩兜底。",
                "proxy": "",
            },
            "enabled": False,  # 需代理,預設關(同 YFinance 口徑),使用者配好 proxy 再開
            "priority": 20,  # US/HK 最後兜底
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # 資金流向資料來源
        {
            "name": "東方財富資金流",
            "type": "capital_flow",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "新浪資金流",
            "type": "capital_flow",
            "provider": "sina",
            "config": {
                "description": "新浪資金流入趨勢(CN,免 key)。作東財之後的第二源,"
                "僅含主力/超大單淨額(無大/中/小單細分)。",
            },
            "enabled": True,
            "priority": 5,  # 東財(0)之後的 CN 第二源
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # 即時行情資料來源
        {
            "name": "騰訊行情",
            "type": "quote",
            "provider": "tencent",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富行情",
            "type": "quote",
            "provider": "eastmoney",
            "config": {"description": "東方財富 push2 即時行情(CN,免 key)。作騰訊之後的 A 股第二源。"},
            "enabled": True,
            "priority": 3,  # 騰訊(0)之後的 CN 第二源(sina/yfinance 不支援 CN)
            "supports_batch": False,  # push2 stock/get 單隻查詢,逐只
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "Sina 行情",
            "type": "quote",
            "provider": "sina",
            "config": {"description": "新浪美股/港股即時行情,免 key 免代理,作騰訊之後的 US/HK 備源。"},
            "enabled": True,
            "priority": 5,   # 騰訊(0)之後
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "YFinance 行情",
            "type": "quote",
            "provider": "yfinance",
            "config": {
                "description": "Yahoo Finance,需 pip install yfinance。適用 HK/US,A 股不可用。",
            },
            "enabled": False,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # 事件日曆資料來源（基於公告結構化）
        {
            "name": "東方財富事件日曆",
            "type": "events",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # 快訊資料來源（7×24 電報，市場級，不按 symbols 過濾）
        {
            "name": "財聯社快訊",
            "type": "flash_news",
            "provider": "cls",
            "config": {"description": "財聯社 7×24 電報(免 key,本地簽名)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "新浪7x24快訊",
            "type": "flash_news",
            "provider": "sina",
            "config": {"description": "新浪財經 7×24 直播,帶關聯個股。"},
            "enabled": True,
            "priority": 5,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "東方財富7x24快訊",
            "type": "flash_news",
            "provider": "eastmoney",
            "config": {"description": "東財 np-weblist 7×24 資訊,與財聯社互備。"},
            "enabled": True,
            "priority": 10,
            "supports_batch": False,
            "test_symbols": [],
        },
        # 基本面資料來源（按 symbol，估值/股本/財報指標）
        {
            "name": "騰訊基本面",
            "type": "fundamentals",
            "provider": "tencent",
            "config": {"description": "騰訊 qt.gtimg 估值快照(CN,免 key):PE/PB/市值。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富基本面",
            "type": "fundamentals",
            "provider": "eastmoney",
            "config": {
                "description": "東財基本面:CN 股本/市值(push2),US/HK 財報指標(GMAININDICATOR)。"
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        # 市場資金面資料來源（龍虎榜/融資融券/股東戶數/分紅/北向資金）
        {
            "name": "東財龍虎榜",
            "type": "dragon_tiger",
            "provider": "eastmoney",
            "config": {
                "description": "東財每日龍虎榜(市場級,需配 test_date 測試)。",
                "test_date": "",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "東財融資融券",
            "type": "margin",
            "provider": "eastmoney",
            "config": {"description": "東財個股融資融券明細(按 symbol)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東財股東戶數",
            "type": "shareholders",
            "provider": "eastmoney",
            "config": {"description": "東財股東戶數變化(按 symbol,季度)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東財分紅",
            "type": "dividend",
            "provider": "eastmoney",
            "config": {"description": "東財分紅送轉歷史(按 symbol)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "同花順北向資金",
            "type": "northbound",
            "provider": "ths",
            "config": {
                "description": "同花順北向資金即時(東財已斷供;深股通近期不可靠)。"
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        # K線截圖資料來源
        {
            "name": "雪球K線截圖",
            "type": "chart",
            "provider": "xueqiu",
            "config": {
                "viewport": {"width": 1280, "height": 900},
                "extra_wait_ms": 3000,
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
        {
            "name": "東方財富K線截圖",
            "type": "chart",
            "provider": "eastmoney",
            "config": {
                "viewport": {"width": 1280, "height": 900},
                "extra_wait_ms": 2000,
            },
            "enabled": False,
            "priority": 1,
            "supports_batch": False,
            "test_symbols": list(DEFAULT_TEST_SYMBOLS),
        },
]


def seed_data_sources(db=None, *, reset_test_symbols: bool = False) -> list[dict]:
    """初始化預置資料來源(按 name+provider 只增不刪的 upsert)。

    db 為 None 時自建獨立 session 並自行 commit/close(相容舊呼叫方式);
    傳入 db 時複用呼叫方 session,不 commit/close,交由呼叫方統一處理
    (供 reconcile_data_sources 在同一事務裡接著做刪孤兒)。

    reset_test_symbols 僅由“恢復預設”入口傳入,用於重置內建源測試股票；普通啟動對帳不覆蓋使用者配置。
    返回本次新增(缺失被補齊)的種子記錄摘要列表 [{"name","type","provider"}, ...]。
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    seeded_missing: list[dict] = []
    for source_data in DATA_SOURCE_SEEDS:
        existing = (
            db.query(DataSource)
            .filter(
                DataSource.name == source_data["name"],
                DataSource.provider == source_data["provider"],
            )
            .first()
        )
        if existing:
            # 恢復預設時只重置測試程式碼；配置、啟用狀態、優先順序等使用者設定仍保留。
            if existing.supports_batch != source_data.get("supports_batch", False):
                existing.supports_batch = source_data.get("supports_batch", False)
            if reset_test_symbols:
                existing.test_symbols = list(source_data.get("test_symbols", []))
            elif not existing.test_symbols:  # 啟動對帳只補空值,不覆蓋使用者配置
                existing.test_symbols = source_data.get("test_symbols", [])
        else:
            db.add(DataSource(**source_data))
            seeded_missing.append(
                {
                    "name": source_data["name"],
                    "type": source_data["type"],
                    "provider": source_data["provider"],
                }
            )

    if owns_session:
        db.commit()
        db.close()

    return seeded_missing


def _seed_providers_by_type() -> dict[str, set[str]]:
    """從 DATA_SOURCE_SEEDS 推導每個 type 當前合法的 provider 集合。"""
    result: dict[str, set[str]] = {}
    for source_data in DATA_SOURCE_SEEDS:
        result.setdefault(source_data["type"], set()).add(source_data["provider"])
    return result


def reconcile_data_sources(db, *, reset_test_symbols: bool = False) -> dict:
    """資料來源表溫和對帳:補缺失預設 + 刪孤兒,保留使用者有效自定義/憑證。

    孤兒判定: legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 內該 type 的 provider 集合;
    DB 行 (type, provider) 不在 legal(type) 內即孤兒。news/chart 等非引擎型別(包內集合為空)的合法性完全由 seed 決定。

    只刪孤兒行,其餘行(含使用者改過 config/priority/enabled 的自定義行)原樣保留。
    reset_test_symbols=True 時,僅覆蓋內建種子的 test_symbols,供“恢復預設”使用。
    """
    from marketdata import PACKAGE_VENDORS_BY_TYPE

    seeded_missing = seed_data_sources(db, reset_test_symbols=reset_test_symbols)
    seed_providers_by_type = _seed_providers_by_type()

    deleted: list[dict] = []
    for row in db.query(DataSource).all():
        legal = PACKAGE_VENDORS_BY_TYPE.get(row.type, frozenset()) | seed_providers_by_type.get(row.type, set())
        if row.provider not in legal:
            deleted.append(
                {"id": row.id, "type": row.type, "provider": row.provider, "name": row.name}
            )
            db.delete(row)

    if seeded_missing:
        logger.info(f"資料來源對帳: 補齊缺失預設 {len(seeded_missing)} 條: {seeded_missing}")
    if deleted:
        logger.info(f"資料來源對帳: 刪除孤兒資料來源 {len(deleted)} 條: {deleted}")

    db.commit()
    return {"deleted": deleted, "seeded_missing": seeded_missing}


def seed_strategies():
    """初始化策略目錄。"""
    ensure_strategy_catalog()
    logger.info("策略目錄初始化完成")


def load_watchlist_for_agent(agent_name: str) -> list[StockConfig]:
    """從資料庫載入某個 Agent 關聯的自選股"""
    db = SessionLocal()
    try:
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = [sa.stock_id for sa in stock_agents]
        if not stock_ids:
            return []

        # 繫結優先：只要綁定了 Agent，就納入執行範圍
        stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
        result = []
        for s in stocks:
            try:
                market = MarketCode(s.market)
            except ValueError:
                market = MarketCode.CN
            result.append(
                StockConfig(
                    symbol=s.symbol,
                    name=s.name,
                    market=market,
                )
            )
        return result
    finally:
        db.close()


def load_portfolio_for_agent(agent_name: str) -> PortfolioInfo:
    """從資料庫載入某個 Agent 關聯股票的持倉資訊（包括多帳戶）"""
    from src.platform.persistence.models import Account, Position

    db = SessionLocal()
    try:
        # 獲取 Agent 關聯的股票 ID
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = set(sa.stock_id for sa in stock_agents)
        if not stock_ids:
            return PortfolioInfo()

        # 獲取所有啟用的帳戶
        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            # 獲取該帳戶中屬於關聯股票的持倉
            positions = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id.in_(stock_ids),
                )
                .all()
            )

            position_infos = []
            for pos in positions:
                stock = pos.stock
                if not stock:
                    continue
                try:
                    market = MarketCode(stock.market)
                except ValueError:
                    market = MarketCode.CN

                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def load_portfolio_for_stock(stock_id: int) -> PortfolioInfo:
    """從資料庫載入單隻股票的持倉資訊"""
    from src.platform.persistence.models import Account, Position

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return PortfolioInfo()

        try:
            market = MarketCode(stock.market)
        except ValueError:
            market = MarketCode.CN

        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id == stock_id,
                )
                .first()
            )

            position_infos = []
            if pos:
                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def _get_proxy() -> str:
    """從 app_settings 獲取 http_proxy"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def _get_app_setting(key: str) -> str:
    """從 app_settings 獲取配置（不存在返回空字串）"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == key).first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def resolve_ai_model(
    agent_name: str, stock_agent_id: int | None = None
) -> tuple[AIModel | None, AIService | None]:
    """解析 AI 模型: stock_agent 覆蓋 → agent 預設 → 系統預設(is_default=True)
    返回 (model, service) 元組"""
    db = SessionLocal()
    try:
        model_id = None

        # 1. stock_agent 級別覆蓋
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.ai_model_id:
                model_id = sa.ai_model_id

        # 2. agent 級別預設
        if not model_id:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.ai_model_id:
                model_id = agent.ai_model_id

        # 3. 系統預設
        if not model_id:
            default_model = db.query(AIModel).filter(AIModel.is_default == True).first()
            if default_model:
                model_id = default_model.id

        # 4. 回退：取第一個
        if not model_id:
            first_model = db.query(AIModel).first()
            if first_model:
                model_id = first_model.id

        if not model_id:
            return None, None

        model = db.query(AIModel).filter(AIModel.id == model_id).first()
        if not model:
            return None, None

        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if model:
            db.expunge(model)
        if service:
            db.expunge(service)
        return model, service
    finally:
        db.close()


def resolve_notify_channels(
    agent_name: str, stock_agent_id: int | None = None
) -> list[NotifyChannel]:
    """解析通知管道: stock_agent 覆蓋 → agent 預設 → 系統預設(is_default=True)"""
    db = SessionLocal()
    try:
        channel_ids = None

        # 1. stock_agent 級別覆蓋
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.notify_channel_ids:
                channel_ids = sa.notify_channel_ids

        # 2. agent 級別預設
        if channel_ids is None:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.notify_channel_ids:
                channel_ids = agent.notify_channel_ids

        # 3. 按 id 列表查詢或取系統預設
        if channel_ids:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.id.in_(channel_ids),
                    NotifyChannel.enabled == True,
                )
                .all()
            )
        else:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.is_default == True,
                    NotifyChannel.enabled == True,
                )
                .all()
            )

        for ch in channels:
            db.expunge(ch)
        return channels
    finally:
        db.close()


def _build_notifier(channels: list[NotifyChannel]) -> NotifierManager:
    """根據解析後的管道列表構建 NotifierManager"""
    settings = Settings()
    # allow UI override via app_settings
    quiet_hours = _get_app_setting("notify_quiet_hours") or settings.notify_quiet_hours
    retry_attempts_raw = _get_app_setting("notify_retry_attempts")
    backoff_raw = _get_app_setting("notify_retry_backoff_seconds")
    overrides_raw = (
        _get_app_setting("notify_dedupe_ttl_overrides")
        or settings.notify_dedupe_ttl_overrides
    )

    try:
        retry_attempts = (
            int(retry_attempts_raw)
            if retry_attempts_raw
            else settings.notify_retry_attempts
        )
    except Exception:
        retry_attempts = settings.notify_retry_attempts
    try:
        retry_backoff_seconds = (
            float(backoff_raw) if backoff_raw else settings.notify_retry_backoff_seconds
        )
    except Exception:
        retry_backoff_seconds = settings.notify_retry_backoff_seconds

    from src.platform.notifications.notify_policy import NotifyPolicy, parse_dedupe_overrides

    policy = NotifyPolicy(
        timezone=settings.app_timezone,
        quiet_hours=quiet_hours,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        dedupe_ttl_overrides=parse_dedupe_overrides(overrides_raw),
    )

    notifier = NotifierManager(policy=policy)
    for ch in channels:
        notifier.add_channel(ch.type, ch.config or {})
    return notifier


def _build_ai_client(model: AIModel | None, service: AIService | None, proxy: str):
    """根據解析後的 model+service 構建帶 failover 的 AI 使用者端。

    主候選沿用四級路由選定的 model+service;備選由 build_failover_client 從庫裡
    其餘模型按優先順序補齊。返回的 FailoverAIClient 與 AIClient 介面相容,可原地替換。
    """
    return build_failover_client(model, service, proxy)


def build_context(agent_name: str, stock_agent_id: int | None = None) -> AgentContext:
    """為指定 Agent 構建執行上下文"""
    settings = Settings()
    watchlist = load_watchlist_for_agent(agent_name)
    portfolio = load_portfolio_for_agent(agent_name)
    proxy = _get_proxy() or settings.http_proxy

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    ai_client = _build_ai_client(model, service, proxy)
    channels = resolve_notify_channels(agent_name, stock_agent_id)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=watchlist)
    return AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        notify_policy=getattr(notifier, "policy", None),
    )


# Agent 登入檔
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    "premarket_outlook": PremarketOutlookAgent,
    "news_digest": NewsDigestAgent,
    "chart_analyst": ChartAnalystAgent,
    "intraday_monitor": IntradayMonitorAgent,
    "tradingagents": TradingAgentsAgent,
}


def build_scheduler() -> AgentScheduler:
    """構建排程器並註冊已啟用的 Agent"""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # 設定 context 構建函式（每次執行時動態獲取最新配置）
    sched.set_context_builder(build_context)

    db = SessionLocal()
    try:
        agent_configs = (
            db.query(AgentConfig)
            .filter(
                AgentConfig.enabled == True,
                AgentConfig.kind == AGENT_KIND_WORKFLOW,
            )
            .all()
        )
        for cfg in agent_configs:
            agent_cls = AGENT_REGISTRY.get(cfg.name)
            if not agent_cls:
                logger.warning(f"Agent {cfg.name} 未在 AGENT_REGISTRY 中註冊")
                continue
            if not cfg.schedule:
                logger.info(f"Agent {cfg.name} 未設定排程計劃，跳過")
                continue

            agent_kwargs = cfg.config or {}
            try:
                agent_instance = (
                    agent_cls(**agent_kwargs) if agent_kwargs else agent_cls()
                )
            except TypeError:
                agent_instance = agent_cls()
            sched.register(
                agent_instance,
                schedule=cfg.schedule,
                execution_mode=cfg.execution_mode or "batch",
            )
    finally:
        db.close()

    return sched


def register_mcp_log_cleanup(sched: AgentScheduler) -> None:
    """Register MCP audit-log retention on the wrapped APScheduler instance.

    ``AgentScheduler`` owns the concrete APScheduler as ``.scheduler``;
    keeping this boundary explicit prevents startup code from accidentally
    calling ``add_job`` on the wrapper itself.
    """
    from src.modules.administration.api.mcp import prune_mcp_logs

    sched.scheduler.add_job(
        prune_mcp_logs,
        "cron",
        hour=4,
        minute=0,
        id="mcp_log_retention",
        replace_existing=True,
    )
    logger.info("MCP 日誌保留期清理任務已註冊")


def reload_scheduler() -> bool:
    """過載排程器（用於配置匯入/批次修改後立即生效）"""
    global scheduler
    try:
        current = globals().get("scheduler")
        if current:
            try:
                current.shutdown()
            except Exception:
                pass
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Agent 排程器已過載")
        return True
    except Exception as e:
        logger.error(f"Agent 排程器過載失敗: {e}")
        return False


def _log_trigger_info(
    agent_name: str,
    stocks: list,
    model: AIModel | None,
    service: AIService | None,
    channels: list[NotifyChannel],
):
    """列印 Agent 觸發時的上下文資訊"""
    stock_names = ", ".join(
        f"{s.name}({s.symbol})" if hasattr(s, "symbol") else str(s) for s in stocks
    )
    ai_info = f"{service.name}/{model.model}" if model and service else "未配置"
    channel_info = ", ".join(ch.name for ch in channels) if channels else "無"
    logger.info(
        f"[觸發] Agent={agent_name} | 股票=[{stock_names}] | AI={ai_info} | 通知=[{channel_info}]"
    )


def get_agent_execution_mode(agent_name: str) -> str:
    """獲取 Agent 的執行模式"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.execution_mode if agent and agent.execution_mode else "batch"
    finally:
        db.close()


def get_agent_config(agent_name: str) -> dict:
    """獲取 Agent 的配置引數"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.config if agent and agent.config else {}
    finally:
        db.close()


async def trigger_agent(agent_name: str) -> str:
    """手動觸發 Agent 執行（根據執行模式處理）"""
    start = time.monotonic()
    trace_id = f"man-{agent_name}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未註冊實際實現")

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent",
        tags={"trigger_source": "manual"},
    ):
        watchlist = load_watchlist_for_agent(agent_name)
        logger.info(
            f"[watchlist] Agent={agent_name} count={len(watchlist)} symbols={[s.symbol for s in watchlist]}"
        )
        if not watchlist:
            return f"Agent {agent_name} 沒有關聯的自選股"

        model, service = resolve_ai_model(agent_name)
        channels = resolve_notify_channels(agent_name)
        _log_trigger_info(agent_name, watchlist, model, service, channels)

        context = build_context(agent_name)
        execution_mode = get_agent_execution_mode(agent_name)
        agent_config = get_agent_config(agent_name)

        # 根據配置初始化 Agent
        if agent_config:
            agent = agent_cls(**agent_config)
        else:
            agent = agent_cls()

        try:
            if execution_mode == "single" and hasattr(agent, "run_single"):
                # 單隻模式：逐只股票分析
                results = []
                for stock in watchlist:
                    result = await agent.run_single(context, stock.symbol)
                    if result:
                        results.append(f"{stock.name}: {result.content[:100]}...")
                msg = "\n\n".join(results) if results else "無異動"
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=msg,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    model_label=context.model_label,
                )
                return msg
            else:
                # 批次模式：所有股票一起分析
                result = await agent.run(context)
                raw = result.raw_data or {}
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=result.content,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    notify_attempted=(
                        "notified" in raw
                        or "notify_error" in raw
                        or "notify_skipped" in raw
                    ),
                    notify_sent=bool(raw.get("notified", False)),
                    model_label=context.model_label,
                )
                return result.content
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise


async def trigger_agent_for_stock(
    agent_name: str,
    stock,
    stock_agent_id: int | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    suppress_notify: bool = False,
    trace_id: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """手動觸發 Agent 執行（單隻股票）"""
    start = time.monotonic()
    trace_id = trace_id or f"man-{agent_name}-{stock.symbol}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未註冊實際實現")
    # 自動排程等不經過 stocks.trigger API 的入口也要擁有同樣的生命週期記錄；
    # 手動入口已提前寫入，這裡冪等呼叫可避免重複 AgentRun。
    try:
        from src.modules.automation.agent_runs import start_agent_run
        start_agent_run(
            agent_name=agent_name,
            trace_id=trace_id,
            trigger_source="manual",
        )
    except Exception as e:
        logger.warning(f"寫 AgentRun running 狀態失敗,不影響主流程: {e}")

    settings = Settings()
    proxy = _get_proxy() or settings.http_proxy

    try:
        market = MarketCode(stock.market)
    except ValueError:
        market = MarketCode.CN

    stock_config = StockConfig(
        symbol=stock.symbol,
        name=stock.name,
        market=market,
    )

    # 載入該股票的持倉資訊
    portfolio = load_portfolio_for_stock(stock.id)

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    channels = [] if suppress_notify else resolve_notify_channels(agent_name, stock_agent_id)
    _log_trigger_info(agent_name, [stock], model, service, channels)

    ai_client = _build_ai_client(model, service, proxy)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=[stock_config])
    context = AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        suppress_notify=suppress_notify,
    )
    # 暴露 trace_id / force_refresh 給 agent(供 TradingAgents 進度回饋 + 快取控制使用)。
    # AgentContext 不強制宣告此欄位,透過 setattr 注入,其他 agent 不受影響。
    setattr(context, "_trace_id", trace_id)
    setattr(context, "_force_refresh", force_refresh)

    # 建立 agent，支援手動觸發引數。TradingAgents 等新 agent 從 AgentConfig 讀 config。
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    elif agent_name == "tradingagents":
        # 從 AgentConfig.config 讀取例項化引數
        agent_kwargs = get_agent_config(agent_name) or {}
        try:
            agent = agent_cls(**agent_kwargs)
        except TypeError:
            agent = agent_cls()
    else:
        agent = agent_cls()

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent_for_stock",
        tags={"trigger_source": "manual", "stock_symbol": stock.symbol},
    ):
        try:
            result = await agent.run(context)
            raw = result.raw_data or {}
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=result.content,
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                notify_attempted=(
                    "notified" in raw
                    or "notify_error" in raw
                    or "notify_skipped" in raw
                ),
                notify_sent=bool(raw.get("notified", False)),
                model_label=context.model_label,
            )
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise

    # 返回詳細結果
    skipped = bool(result.raw_data.get("skipped", False))
    should_alert = bool(
        result.raw_data.get("should_alert", False if skipped else True)
    )
    return {
        "code": 0 if not skipped else 1001001,
        "success": not skipped,
        "message": result.content if skipped else "ok",
        "title": result.title,
        "content": result.content,
        "should_alert": should_alert,
        "notified": result.raw_data.get("notified", False),
        "skipped": skipped,
    }


@asynccontextmanager
async def lifespan(app):
    """應用生命週期: 初始化 + 啟動排程器"""
    init_db()
    setup_logging()
    # OTel 匯出(可選,預設關閉):僅當配置了 OTEL_EXPORTER_OTLP_ENDPOINT 且裝了
    # opentelemetry SDK 時啟用,否則靜默 no-op,不影響現有部署。
    try:
        from src.platform.observability.otel import init_otel

        init_otel()
    except Exception as e:  # 兜底:OTel 初始化異常絕不阻斷服務啟動
        logger.warning(f"OTel 初始化跳過: {e}")
    setup_proxy()  # 設定程式 env 代理(HTTP_PROXY/NO_PROXY);所有 httpx(trust_env=True)據此走代理
    setup_ssl()
    setup_playwright()

    # 從環境變數初始化認證（Docker 部署用）
    from src.modules.administration.api.auth import init_auth_from_env

    db = SessionLocal()
    try:
        if init_auth_from_env(db):
            logger.info("已從環境變數初始化認證帳號")
    finally:
        db.close()

    seed_agents()
    try:
        db = SessionLocal()
        try:
            reconcile_data_sources(db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"資料來源對帳失敗,跳過(不阻斷啟動): {e}")
    seed_strategies()
    seed_sample_stocks()

    # 啟動時回填歷史 TradingAgents 決策到建議池(stock_suggestions)
    # 早期 TA 執行沒寫建議池,這次啟動一次性補齊,讓「AI 建議」面板能看到。
    # 冪等:已存在不重複寫;每次啟動重跑代價極低(只查最近 7 天 + dedupe)。
    try:
        from src.modules.automation.tradingagents.operations import backfill_tradingagents_suggestions
        backfill_tradingagents_suggestions(days=7)
    except Exception as e:
        logger.warning(f"TradingAgents 建議回填失敗,跳過: {e}")

    # 後臺重新整理股票列表快取
    import threading
    from src.platform.marketdata.stock_list import get_stock_list, refresh_stock_list

    def refresh_stock_cache():
        stocks = get_stock_list()
        if not stocks or len([s for s in stocks if s["market"] == "CN"]) == 0:
            logger.info("股票列表快取為空或缺少 A 股，後臺重新整理中...")
            refresh_stock_list()

    threading.Thread(target=refresh_stock_cache, daemon=True).start()

    # 交易日曆預熱(判斷週末/法定節假日是否開市)。拉取失敗會自動降級為只判週末,
    # 因此這裡不阻塞啟動,交給後臺任務;之後每日 03:00 由上下文維護排程器重新整理。
    try:
        from src.platform.scheduling.trading_calendar import refresh as refresh_trading_calendar

        asyncio.create_task(refresh_trading_calendar())
    except Exception as e:
        logger.warning(f"交易日曆預熱排程失敗(降級為只判週末): {e}")

    global scheduler, price_alert_scheduler, paper_trading_scheduler, context_maintenance_scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Agent 排程器已啟動")
    try:
        settings = Settings()
        price_alert_scheduler = PriceAlertScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        price_alert_scheduler.start()
        logger.info("價格提醒排程器已啟動")
    except Exception as e:
        logger.error(f"價格提醒排程器啟動失敗: {e}")
    try:
        settings = Settings()
        paper_trading_scheduler = PaperTradingScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        paper_trading_scheduler.start()
        logger.info("模擬交易排程器已啟動")
    except Exception as e:
        logger.error(f"模擬交易排程器啟動失敗: {e}")
    try:
        settings = Settings()
        context_maintenance_scheduler = ContextMaintenanceScheduler(
            timezone=settings.app_timezone,
            eval_interval_hours=6,
            snapshot_retention_days=180,
            outcome_retention_days=365,
        )
        context_maintenance_scheduler.start()
        logger.info("上下文維護排程器已啟動")
    except Exception as e:
        logger.error(f"上下文維護排程器啟動失敗: {e}")
    # MCP 呼叫日誌保留期清理:每日 04:00 清理超期審計記錄
    try:
        register_mcp_log_cleanup(scheduler)
    except Exception as e:
        logger.error(f"MCP 日誌清理任務註冊失敗: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent 排程器已關閉")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("價格提醒排程器已關閉")
    if paper_trading_scheduler:
        paper_trading_scheduler.shutdown()
        logger.info("模擬交易排程器已關閉")
    if context_maintenance_scheduler:
        context_maintenance_scheduler.shutdown()
        logger.info("上下文維護排程器已關閉")


# 模組級 app 例項，供 uvicorn reload 使用
from src.bootstrap.application import app  # noqa: E402

app.router.lifespan_context = lifespan

# 生產環境靜態檔案服務
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # SPA 路由：所有非 API 請求返回 index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = os.path.join(static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))

    logger.info(f"靜態檔案服務已啟用: {static_dir}")


if __name__ == "__main__":
    print("盯盤俠啟動: http://127.0.0.1:8000")
    print("API 檔案: http://127.0.0.1:8000/docs")
    # 生產(Docker `python server.py`)不應開 reload:uvicorn 檔案監聽會多起一個 reloader
    # 子程式、浪費資源,且監聽 data/ 寫入易誤觸發重啟。本地熱過載用 `make dev-api`
    # (uvicorn --reload),或顯式設 DEV_RELOAD=1。
    _dev_reload = os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=_dev_reload,
        reload_dirs=["src", "."] if _dev_reload else None,
        reload_excludes=["data/*", "frontend/*", ".claude/*"] if _dev_reload else None,
    )
