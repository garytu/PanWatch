"""K線圖截圖採集器 - 基於 Playwright"""
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.platform.marketdata.cn_symbol import get_cn_prefix

logger = logging.getLogger(__name__)

# 截圖儲存目錄
SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "panwatch_screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# 預設配置
DEFAULT_CONFIG = {
    "viewport": {"width": 1280, "height": 900},
    "wait_selector": ".quote_title",  # 等待頁面主體載入
    "extra_wait_ms": 3000,  # 等待圖表渲染
}


@dataclass
class ChartScreenshot:
    """K線圖截圖"""
    symbol: str
    name: str
    market: str
    filepath: str
    period: str = "daily"  # daily/weekly/monthly
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def exists(self) -> bool:
        return os.path.exists(self.filepath)


class ScreenshotCollector:
    """
    K線圖截圖採集器

    使用 Playwright 擷取東方財富 K 線圖
    URL 格式:
    - A股: https://quote.eastmoney.com/{sh|sz}{symbol}.html
    - 港股: https://quote.eastmoney.com/hk/{symbol}.html
    """

    def __init__(self, config: dict | None = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """懶載入初始化 Playwright（帶反檢測設定）"""
        if self._browser is not None:
            return

        try:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()

            # 使用反檢測設定啟動瀏覽器
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ]
            )
            logger.info("Playwright 瀏覽器已啟動")
        except ImportError:
            raise RuntimeError("請先安裝 playwright: pip install playwright && playwright install chromium")
        except Exception as e:
            logger.error(f"Playwright 啟動失敗: {e}")
            raise

    def _get_url(self, symbol: str, market: str, provider: str = "xueqiu") -> str:
        """生成 K 線圖頁面 URL"""
        if provider == "sina":
            return self._get_sina_url(symbol, market)
        elif provider == "xueqiu":
            return self._get_xueqiu_url(symbol, market)
        else:
            return self._get_eastmoney_url(symbol, market)

    def _get_sina_url(self, symbol: str, market: str) -> str:
        """新浪財經 URL"""
        if market.upper() == "HK":
            return f"https://stock.finance.sina.com.cn/hkstock/quotes/{symbol}.html"
        # A股
        prefix = get_cn_prefix(symbol)
        return f"https://finance.sina.com.cn/realstock/company/{prefix}{symbol}/nc.shtml"

    def _get_eastmoney_url(self, symbol: str, market: str) -> str:
        """東方財富 URL"""
        if market.upper() == "HK":
            return f"https://quote.eastmoney.com/hk/{symbol}.html"
        # A股
        prefix = get_cn_prefix(symbol)
        return f"https://quote.eastmoney.com/{prefix}{symbol}.html"

    def _get_xueqiu_url(self, symbol: str, market: str) -> str:
        """雪球 URL"""
        if market.upper() == "HK":
            return f"https://xueqiu.com/S/{symbol}"
        # A股
        prefix = get_cn_prefix(symbol, upper=True)
        return f"https://xueqiu.com/S/{prefix}{symbol}"

    async def capture(
        self,
        symbol: str,
        name: str,
        market: str = "CN",
        period: str = "daily",
        provider: str = "xueqiu",
    ) -> ChartScreenshot | None:
        """
        擷取單隻股票的 K 線圖

        Args:
            symbol: 股票程式碼
            name: 股票名稱
            market: 市場 (CN/HK)
            period: K線週期 (daily/weekly/monthly)
            provider: 資料來源 (xueqiu/eastmoney)

        Returns:
            ChartScreenshot 或 None（失敗時）
        """
        await self._ensure_browser()

        url = self._get_url(symbol, market, provider)
        filepath = str(SCREENSHOT_DIR / f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")

        try:
            # 使用真實的 User-Agent 和反檢測設定
            context = await self._browser.new_context(
                viewport=self.config["viewport"],
                locale="zh-CN",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                java_script_enabled=True,
                bypass_csp=True,
            )
            page = await context.new_page()

            # 注入反檢測指令碼
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
                window.chrome = { runtime: {} };
            """)

            logger.debug(f"正在載入 {name}({symbol}) K線圖: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待頁面主體載入
            try:
                await page.wait_for_selector(
                    self.config["wait_selector"],
                    timeout=15000,
                    state="visible",
                )
            except Exception:
                # 備選：等待任意內容載入
                await page.wait_for_load_state("networkidle", timeout=10000)

            # 額外等待渲染
            await page.wait_for_timeout(self.config["extra_wait_ms"])

            # 根據資料來源執行不同的截圖邏輯
            if provider == "xueqiu":
                await self._capture_xueqiu(page, filepath, period)
            elif provider == "sina":
                await self._capture_sina(page, filepath, period)
            else:
                await self._capture_eastmoney(page, filepath, period)

            await context.close()

            logger.info(f"截圖成功: {name}({symbol}) -> {filepath}")
            return ChartScreenshot(
                symbol=symbol,
                name=name,
                market=market,
                filepath=filepath,
                period=period,
            )

        except Exception as e:
            logger.error(f"截圖失敗 {name}({symbol}): {e}")
            return None

    async def _capture_xueqiu(self, page, filepath: str, period: str):
        """雪球截圖邏輯"""
        # 等待頁面載入
        await page.wait_for_timeout(1000)

        # 關閉所有可能的彈跳視窗
        await self._close_xueqiu_popups(page)

        # 等待圖表載入
        try:
            await page.wait_for_selector(".stock-chart", timeout=10000)
        except Exception:
            pass

        # 切換到日K（預設是分時圖）
        await self._switch_to_daily_kline(page)

        # 如果需要其他週期再切換
        if period == "weekly":
            await self._switch_period_xueqiu(page, "weekly")
        elif period == "monthly":
            await self._switch_period_xueqiu(page, "monthly")

        # 直接擷取固定區域（K線圖區域）
        await page.screenshot(
            path=filepath,
            clip={"x": 250, "y": 80, "width": 660, "height": 720}
        )
        logger.debug("雪球 K 線圖截圖完成")

    async def _capture_sina(self, page, filepath: str, period: str):
        """新浪財經截圖邏輯"""
        # 等待頁面載入
        try:
            await page.wait_for_selector("#kline_container", timeout=10000)
        except Exception:
            pass

        # 擷取 K 線圖區域
        try:
            chart = await page.query_selector("#kline_container")
            if chart:
                await chart.screenshot(path=filepath)
                return
        except Exception:
            pass

        await page.screenshot(path=filepath, full_page=False)

    async def _capture_eastmoney(self, page, filepath: str, period: str):
        """東方財富截圖邏輯"""
        # 滾動到 K 線圖區域
        try:
            kline_area = await page.query_selector("#app > div > div > div.quote_title.self_clearfix")
            if kline_area:
                await kline_area.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # 嘗試切換週期
        if period != "daily":
            await self._switch_period(page, period)

        # 截圖 K 線圖區域
        try:
            kline_container = await page.query_selector("#kline_div")
            if kline_container:
                await kline_container.screenshot(path=filepath)
                return
        except Exception:
            pass

        await page.screenshot(path=filepath, full_page=False)

    async def _close_xueqiu_popups(self, page):
        """關閉雪球所有彈跳視窗"""
        # 多次嘗試關閉各種彈跳視窗
        for _ in range(5):
            closed = False

            # 1. 關閉登入彈跳視窗（點選"跳過"）
            try:
                skip_btn = await page.query_selector('text="跳過"')
                if skip_btn and await skip_btn.is_visible():
                    await skip_btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug("已關閉登入彈跳視窗")
                    closed = True
            except Exception:
                pass

            # 2. 關閉邀請加群彈跳視窗（點選 X 按鈕）
            try:
                # 彈跳視窗右上角的關閉按鈕
                close_btns = await page.query_selector_all('svg, .close, [class*="close"], [class*="Close"]')
                for btn in close_btns:
                    try:
                        if await btn.is_visible():
                            box = await btn.bounding_box()
                            # 只點選在彈跳視窗區域內的關閉按鈕
                            if box and box["x"] > 200 and box["y"] < 500:
                                await btn.click()
                                await page.wait_for_timeout(500)
                                logger.debug("已關閉彈跳視窗")
                                closed = True
                                break
                    except Exception:
                        continue
            except Exception:
                pass

            # 3. 按 ESC 鍵
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # 4. 點選遮罩層關閉
            try:
                mask = await page.query_selector('.modal-mask, .overlay, [class*="mask"]')
                if mask and await mask.is_visible():
                    await mask.click()
                    await page.wait_for_timeout(500)
                    closed = True
            except Exception:
                pass

            if not closed:
                break
            await page.wait_for_timeout(300)

    async def _switch_to_daily_kline(self, page):
        """雪球切換到日K線圖"""
        try:
            # 點選"日K"按鈕
            daily_btn = await page.query_selector('text="日K"')
            if daily_btn and await daily_btn.is_visible():
                await daily_btn.click()
                await page.wait_for_timeout(1500)
                logger.debug("已切換到日K線圖")
        except Exception as e:
            logger.debug(f"切換日K失敗: {e}")

    async def _switch_period_xueqiu(self, page, period: str):
        """雪球切換K線週期"""
        period_text = {"weekly": "周K", "monthly": "月K"}.get(period)
        if not period_text:
            return
        try:
            btn = await page.query_selector(f'text="{period_text}"')
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1500)
                logger.debug(f"已切換到{period_text}")
        except Exception:
            pass

    async def _close_popups(self, page):
        """關閉彈跳視窗廣告"""
        # 常見的關閉按鈕選擇器
        close_selectors = [
            'text="關閉"',
            'text="×"',
            'text="X"',
            '.close-btn',
            '.modal-close',
            '[class*="close"]',
            'button:has-text("關閉")',
            'a:has-text("關閉")',
            '.layui-layer-close',
            '.popup-close',
        ]

        for selector in close_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(500)
                    logger.debug(f"關閉彈跳視窗: {selector}")
            except Exception:
                continue

        # 按 ESC 鍵關閉可能的彈跳視窗
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

    async def _switch_period(self, page, period: str):
        """切換K線週期"""
        period_map = {
            "weekly": ["周K", "週線", "week"],
            "monthly": ["月K", "月線", "month"],
        }
        keywords = period_map.get(period, [])

        for keyword in keywords:
            try:
                btn = await page.query_selector(f'text="{keyword}"')
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(1000)
                    return
            except Exception:
                continue

    async def capture_batch(
        self,
        stocks: list[dict],
        period: str = "daily",
        provider: str = "xueqiu",
    ) -> list[ChartScreenshot]:
        """
        批次擷取K線圖

        Args:
            stocks: 股票列表，每項包含 symbol, name, market
            period: K線週期
            provider: 資料來源 (xueqiu/eastmoney)

        Returns:
            ChartScreenshot 列表
        """
        results = []
        for stock in stocks:
            screenshot = await self.capture(
                symbol=stock.get("symbol", ""),
                name=stock.get("name", ""),
                market=stock.get("market", "CN"),
                period=period,
                provider=provider,
            )
            if screenshot:
                results.append(screenshot)

        return results

    def cleanup_old_screenshots(self, max_age_hours: int = 24):
        """清理過期截圖"""
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        cleaned = 0

        for filepath in SCREENSHOT_DIR.glob("*.png"):
            if filepath.stat().st_mtime < cutoff:
                try:
                    filepath.unlink()
                    cleaned += 1
                except Exception as e:
                    logger.debug(f"清理截圖失敗 {filepath}: {e}")

        if cleaned:
            logger.info(f"清理了 {cleaned} 張過期截圖")

    async def close(self):
        """關閉瀏覽器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
