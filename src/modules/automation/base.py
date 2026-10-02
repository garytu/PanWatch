import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from src.platform.ai.ai_client import AIClient
from src.platform.notifications.notifier import NotifierManager
from src.platform.runtime.config import AppConfig, StockConfig
from src.platform.marketdata.models import MarketCode
from src.platform.notifications.notify_dedupe import build_notify_dedupe_key, check_and_mark_notify
from src.platform.notifications.notify_policy import NotifyPolicy
from src.platform.observability.log_context import log_context

logger = logging.getLogger(__name__)


@dataclass
class PositionInfo:
    """單個持倉資訊"""

    account_id: int
    account_name: str
    stock_id: int
    symbol: str
    name: str
    market: MarketCode
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str = "swing"  # short: 短線, swing: 波段, long: 長線

    @property
    def cost_value(self) -> float:
        """持倉成本"""
        return self.cost_price * self.quantity


@dataclass
class AccountInfo:
    """帳戶資訊"""

    id: int
    name: str
    available_funds: float
    positions: list[PositionInfo] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        """帳戶總持倉成本"""
        return sum(p.cost_value for p in self.positions)


@dataclass
class PortfolioInfo:
    """持倉組合資訊"""

    accounts: list[AccountInfo] = field(default_factory=list)

    @property
    def total_available_funds(self) -> float:
        """總可用資金"""
        return sum(a.available_funds for a in self.accounts)

    @property
    def total_cost(self) -> float:
        """總持倉成本"""
        return sum(a.total_cost for a in self.accounts)

    @property
    def all_positions(self) -> list[PositionInfo]:
        """所有持倉列表"""
        result = []
        for acc in self.accounts:
            result.extend(acc.positions)
        return result

    def get_positions_for_stock(self, symbol: str) -> list[PositionInfo]:
        """獲取某隻股票在各帳戶的持倉"""
        return [p for p in self.all_positions if p.symbol == symbol]

    def get_aggregated_position(self, symbol: str) -> dict | None:
        """
        獲取某隻股票的彙總持倉（合併所有帳戶）
        返回: {"symbol", "name", "total_quantity", "avg_cost", "total_cost", "trading_style", "positions"}
        """
        positions = self.get_positions_for_stock(symbol)
        if not positions:
            return None

        total_quantity = sum(p.quantity for p in positions)
        total_cost = sum(p.cost_value for p in positions)
        avg_cost = total_cost / total_quantity if total_quantity > 0 else 0
        # 取第一個持倉的交易風格（如果同一股票在多個帳戶有不同風格，優先取短線）
        trading_style = positions[0].trading_style
        for p in positions:
            if p.trading_style == "short":
                trading_style = "short"
                break

        return {
            "symbol": symbol,
            "name": positions[0].name,
            "market": positions[0].market,
            "total_quantity": total_quantity,
            "avg_cost": avg_cost,
            "total_cost": total_cost,
            "trading_style": trading_style,
            "positions": positions,
        }

    def has_position(self, symbol: str) -> bool:
        """是否持有某隻股票"""
        return any(p.symbol == symbol for p in self.all_positions)


class AgentContext:
    """Agent 執行時上下文"""

    def __init__(
        self,
        ai_client: "AIClient",
        notifier: NotifierManager,
        config: AppConfig,
        portfolio: PortfolioInfo | None = None,
        model_label: str = "",
        notify_policy: NotifyPolicy | None = None,
        suppress_notify: bool = False,
    ):
        self.ai_client = ai_client
        self.notifier = notifier
        self.config = config
        self.portfolio = portfolio if portfolio is not None else PortfolioInfo()
        # 主模型標籤(初始);實際使用模型由 ai_client 在 failover 後覆蓋。
        self._primary_model_label = model_label
        self.notify_policy = notify_policy
        self.suppress_notify = suppress_notify

    @property
    def model_label(self) -> str:
        """實際使用的模型標籤。

        failover 使用者端會把真正跑通的候選記在 used_model_label;若不存在(普通
        AIClient)則回退到路由選定的主模型標籤。這樣 footer 與 agent_runs 落庫
        都能反映"實際用了哪個模型",路由過程透明可觀測。
        """
        used = getattr(self.ai_client, "used_model_label", "")
        return used or self._primary_model_label

    @property
    def watchlist(self) -> list[StockConfig]:
        return self.config.watchlist


@dataclass
class AnalysisResult:
    """分析結果"""

    agent_name: str
    title: str
    content: str
    # 通知專用內容(完整、不截斷);為空時通知回退用 content。
    # 深度分析用它推送完整四位分析師觀點,而彈跳視窗 content 保持精簡。
    notify_content: str | None = None
    raw_data: dict = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class BaseAgent(ABC):
    """Agent 抽象基類"""

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    async def collect(self, context: AgentContext) -> dict:
        """採集資料"""
        ...

    @abstractmethod
    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        構建 prompt。

        Returns:
            (system_prompt, user_content)
        """
        ...

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """呼叫 AI 分析"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        # 標題含股票資訊
        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        # 結尾附 AI 模型資訊
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """是否需要通知，子類可重寫"""
        return True

    def _notify_dedupe_ttl_minutes(self, context: AgentContext) -> int:
        """Notification idempotency window (minutes).

        P0 policy: per-agent defaults to avoid duplicate notifications.
        """

        if self.name in ("daily_report", "premarket_outlook"):
            default = 12 * 60
        elif self.name == "news_digest":
            default = 60
        elif self.name == "chart_analyst":
            default = 6 * 60
        # Intraday uses its own per-stock throttle.
        elif self.name == "intraday_monitor":
            default = 30
        elif self.name == "tradingagents":
            # 深度分析單次成本高,同標的 12 小時內不重複推送
            default = 12 * 60
        else:
            default = 60

        policy = getattr(context, "notify_policy", None)
        if policy:
            try:
                return policy.dedupe_ttl_minutes(self.name, default)
            except Exception:
                return default
        return default

    async def run(self, context: AgentContext) -> AnalysisResult:
        """標準執行流程"""
        logger.info(f"Agent [{self.display_name}] 開始執行")

        try:
            data = await self.collect(context)
            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                with log_context(
                    event="notify_skipped",
                    notify_status="skipped",
                    notify_reason="suppressed",
                ):
                    logger.info(f"Agent [{self.display_name}] 本次觸發已停用通知")
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            notified = False
            if await self.should_notify(result):
                # Quiet hours: skip sending without marking as error.
                policy = getattr(context, "notify_policy", None)
                if policy:
                    try:
                        if policy.is_quiet_now():
                            with log_context(
                                event="notify_skipped",
                                notify_status="skipped",
                                notify_reason="quiet_hours",
                            ):
                                logger.info(f"Agent [{self.display_name}] 靜默時段跳過通知")
                            result.raw_data["notified"] = False
                            result.raw_data["notify_skipped"] = "quiet_hours"
                            return result
                    except Exception:
                        pass

                # Global notification dedupe (idempotency):
                # avoids repeated pushes when an agent is triggered multiple times.
                ttl = self._notify_dedupe_ttl_minutes(context)
                dedupe_key = build_notify_dedupe_key(
                    self.name, result.title, result.notify_content or result.content
                )
                scope = f"__notify__:{dedupe_key}"
                allowed = check_and_mark_notify(
                    agent_name=self.name,
                    scope=scope,
                    ttl_minutes=ttl,
                    mark=False,
                )
                if not allowed:
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason="deduped",
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] 通知去重命中，跳過傳送 (ttl={ttl}m)"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = "deduped"
                    return result

                with log_context(event="notify_send", notify_status="attempted"):
                    logger.info(f"Agent [{self.display_name}] 開始傳送通知")
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.notify_content or result.content,
                    result.images,
                )
                if notify_result.get("skipped"):
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason=str(notify_result.get("skipped") or ""),
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] 通知已跳過: {notify_result.get('skipped')}"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = notify_result.get("skipped")
                    return result

                notified = bool(notify_result.get("success"))
                if notified:
                    with log_context(
                        event="notify_sent",
                        notify_status="sent",
                    ):
                        logger.info(f"Agent [{self.display_name}] 通知已傳送")
                    # Mark dedupe only after a successful send.
                    check_and_mark_notify(
                        agent_name=self.name,
                        scope=scope,
                        ttl_minutes=ttl,
                        mark=True,
                    )
                else:
                    notify_error = notify_result.get("error") or "未知錯誤"
                    with log_context(
                        event="notify_failed",
                        notify_status="failed",
                        notify_reason=str(notify_error),
                    ):
                        logger.error(
                            f"Agent [{self.display_name}] 通知傳送失敗: {notify_error}"
                        )
                    result.raw_data["notify_error"] = notify_error
            else:
                logger.info(f"Agent [{self.display_name}] 無需通知")

            # 記錄是否傳送了通知
            result.raw_data["notified"] = notified
            return result

        except Exception as e:
            logger.error(f"Agent [{self.display_name}] 執行失敗: {e}")
            raise
