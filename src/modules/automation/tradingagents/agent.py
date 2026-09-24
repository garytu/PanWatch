"""TradingAgentsAgent — PanWatch 的 BaseAgent 子類,整合 TauricResearch/TradingAgents。

設計要點(詳見 .docs/tradingagents/02-technical-design.md):
1. collect() 走 PanWatch Provider Orchestrator,4 類資料併發拉
2. analyze() 重寫,不走單次 ai_client.chat,而是調 TradingAgentsGraph
3. monkeypatch route_to_vendor 讓 TradingAgents 拿到 PanWatch 資料(A 股專用)
4. progress callback + cost tracker + 月度預算 + 同日快取
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.modules.automation.base import AgentContext, AnalysisResult, BaseAgent
from src.modules.automation.tradingagents.observability import (
    check_budget,
    estimate_cost,
    get_today_cache_key,
)
from src.modules.automation.tradingagents.runtime_support import (
    VALID_ANALYSTS,
    apply_compat_patches,
    build_ta_llm_config,
    inject_api_key_env,
)
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.observability import PanWatchProgressHandler
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.toolkit_adapter import (
    panwatch_data_context,
    patch_route_to_vendor,
)
from src.modules.research.analysis_history import get_analysis, save_analysis

logger = logging.getLogger(__name__)

__all__ = ["TradingAgentsAgent", "TradingAgentsUnavailable"]


def get_market_data():
    """lazy import,便於測試 monkeypatch(module 級)。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g

    return _g()


class TradingAgentsUnavailable(RuntimeError):
    """tradingagents 庫未安裝或上游 API 變更導致不可用。"""


def _bounded_graph_class(graph_cls):
    """讓 TradingAgentsGraph 把請求邊界傳給 LangChain LLM 使用者端。

    TradingAgents 0.5.0 已支援 ``llm_max_retries``/``max_tokens``，但當前
    版本的 ``_get_provider_kwargs`` 尚未讀取自定義 timeout。透過一個很小的
    子類適配該差異，避免直接修改 site-packages，也相容後續上游自行支援
    timeout 的版本。
    """

    class BoundedTradingAgentsGraph(graph_cls):
        def _get_provider_kwargs(self):
            kwargs = dict(super()._get_provider_kwargs())
            timeout = self.config.get("llm_timeout_seconds")
            if timeout is not None and timeout != "":
                kwargs["timeout"] = float(timeout)
            return kwargs

    BoundedTradingAgentsGraph.__name__ = (
        f"Bounded{getattr(graph_cls, '__name__', 'TradingAgentsGraph')}"
    )
    return BoundedTradingAgentsGraph


class TradingAgentsAgent(BaseAgent):
    name = "tradingagents"
    display_name = "TradingAgents 深度分析"
    description = "多 Agent 投資決策框架,3-5 分鐘,~$0.05/次 (deepseek-chat)"

    def __init__(
        self,
        analyst_types: list[str] | None = None,
        debate_rounds: int = 1,
        monthly_budget_usd: float = 10.0,
        over_budget_action: str = "reject",  # reject / warn / continue
        cache_ttl_hours: int = 12,
        output_language: str = "Chinese",
        deep_model: str | None = None,    # 推理/辯論/PM 用的強模型 (留空走預設)
        quick_model: str | None = None,   # 分析師工具呼叫用的快模型 (留空 = deep_model)
        timeout_minutes: int = 30,        # 整個流程硬超時;0.3.0 工具鏈更重,預設提到 30 min
        collection_timeout_seconds: int = 45,  # 單個外部資料來源採集硬超時
        emit_paper_trading_signal: bool = False,  # 是否把 BUY 決策寫入 StrategySignalRun 驅動模擬交易
        enable_sec_edgar: bool = False,   # 美股財報可顯式優先使用 SEC EDGAR
        holding_period_days: int = 5,     # 上游決策質量回測/持倉期限語義
        llm_timeout_seconds: int = 120,   # 單次 LLM 請求硬超時,避免圖卡死
        llm_max_retries: int = 0,         # 深度分析不在圖內重複重試供應商請求
        llm_max_tokens: int = 4096,       # 限制推理/報告輸出,避免閘道器空閒超時
    ):
        # 校驗分析師配置
        analysts = list(analyst_types or sorted(VALID_ANALYSTS))
        invalid = [a for a in analysts if a not in VALID_ANALYSTS]
        if invalid:
            raise ValueError(
                f"非法 analyst 名: {invalid}; "
                f"合法值: {sorted(VALID_ANALYSTS)}"
            )

        self.analyst_types = analysts
        self.debate_rounds = max(1, int(debate_rounds))
        self.monthly_budget_usd = float(monthly_budget_usd)
        self.over_budget_action = over_budget_action
        self.cache_ttl_hours = max(0, int(cache_ttl_hours))
        self.output_language = output_language
        self.deep_model = (deep_model or "").strip() or None
        self.quick_model = (quick_model or "").strip() or None
        self.timeout_minutes = max(1, int(timeout_minutes))
        self.collection_timeout_seconds = max(5, int(collection_timeout_seconds))
        self.emit_paper_trading_signal = bool(emit_paper_trading_signal)
        self.enable_sec_edgar = bool(enable_sec_edgar)
        self.holding_period_days = max(1, int(holding_period_days))
        self.llm_timeout_seconds = max(1, int(llm_timeout_seconds))
        self.llm_max_retries = max(0, int(llm_max_retries))
        self.llm_max_tokens = max(256, int(llm_max_tokens))

        # 軟依賴檢測
        self._available, self._import_error = self._check_availability()

    # ---- BaseAgent 抽象方法 ----

    async def collect(self, context: AgentContext) -> dict:
        """從 PanWatch 資料體系收集資料,併發拉 4 類(走 marketdata 包)。"""
        if not context.watchlist:
            raise ValueError("TradingAgents 需要至少 1 只股票")
        # 單隻標的為粒度;若 watchlist 多隻,取第一隻
        stock = context.watchlist[0]

        from src.platform.marketdata.marketdata_client import _quote_to_row

        trace_id = getattr(context, "_trace_id", "")
        if not isinstance(trace_id, str) or not trace_id:
            trace_id = self._make_trace_id(stock.symbol)
        progress_handler = PanWatchProgressHandler(trace_id, self.name)
        setattr(context, "_progress_handler", progress_handler)
        progress_handler.emit("data_collection", "stage_start", symbol=stock.symbol)

        async def _source(name: str, fn, fallback):
            progress_handler.emit("data_collection", "source_start", source=name)
            try:
                value = await asyncio.wait_for(
                    asyncio.to_thread(fn),
                    timeout=self.collection_timeout_seconds,
                )
                if name in {"quote", "klines"} and not value:
                    progress_handler.emit(
                        "data_collection",
                        "source_error",
                        source=name,
                        error="empty result",
                    )
                else:
                    progress_handler.emit("data_collection", "source_end", source=name)
                return value
            except Exception as e:
                # 429、網路超時和單源解析錯誤都隻影響該源，不阻塞整個分析。
                logger.warning(f"[TA] 資料來源 {name} 失敗,使用空結果: {e}")
                progress_handler.emit(
                    "data_collection",
                    "source_error",
                    source=name,
                    error=str(e)[:200],
                )
                return fallback

        try:
            md = get_market_data()
            sym, mkt = stock.symbol, stock.market.value
            from src.platform.marketdata.collectors.kline_collector import kline_source

            with kline_source(f"tradingagents:{trace_id}"):
                quotes, klines_list, cf, events_list = await asyncio.gather(
                    _source("quote", lambda: md.quotes([sym], market=mkt), []),
                    # 一次準備足夠驗證快照和 200 日均線使用的歷史，後續 analyst
                    # 直接複用這份快取，不再重複請求 750 日 K 線。
                    _source("klines", lambda: md.klines(sym, market=mkt, days=750), []),
                    _source("capital_flow", lambda: md.capital_flow(sym, market=mkt), None),
                    _source("events", lambda: md.events([sym], market=mkt, since_days=30), []),
                )
        except Exception as e:
            logger.warning(f"[TA] 初始化資料來源失敗,使用空結果: {e}")
            quotes, klines_list, cf, events_list = [], [], None, []
        try:
            quote_dict = _quote_to_row(quotes[0]) if quotes else {}
        except Exception as e:
            logger.warning(f"[TA] 行情結果解析失敗,使用空結果: {e}")
            quote_dict = {}
        capital_list = [cf] if cf else []

        # A 股 fetch 真實財報(akshare),非 A 股留空
        financial: dict | None = None
        if stock.market.value == "CN" and stock.symbol.isdigit() and len(stock.symbol) == 6:
            try:
                from src.modules.automation.tradingagents.data_context import fetch_financial_abstract
                financial = await _source(
                    "financial",
                    lambda: fetch_financial_abstract(stock.symbol),
                    None,
                )
            except Exception as e:
                logger.debug(f"[TA] 財報模組不可用,跳過: {e}")

        # 預算技術指標(MA/MACD/RSI/KDJ/BOLL),給 get_indicators 工具用
        technical = None
        try:
            from src.platform.marketdata.collectors.kline_collector import KlineCollector
            technical = await _source(
                "technical",
                lambda: KlineCollector(stock.market).get_technical_indicators(
                    stock.symbol,
                    klines=klines_list or None,
                ),
                None,
            )
        except Exception as e:
            logger.debug(f"[TA] 技術指標模組不可用,跳過: {e}")
        progress_handler.emit("data_collection", "stage_end", symbol=stock.symbol)

        return {
            "stock": stock,
            "quote": quote_dict,
            "klines": klines_list,
            "capital_flow": capital_list,
            "events": events_list,
            "financial": financial,
            "technical": technical,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        # BaseAgent 抽象要求,但本 agent 不走單次 prompt
        return "", ""

    async def run_single(self, context: AgentContext, symbol: str) -> AnalysisResult:
        """單隻股票模式入口 — 供 AgentScheduler 排程時按股票迭代呼叫。

        典型場景:盤前自動跑使用者繫結到 tradingagents 的核心倉位股票。
        實現:過濾 watchlist 到指定 symbol,然後走標準 run() 流程。
        """
        # 找到目標 stock
        targets = [s for s in context.watchlist if s.symbol == symbol]
        if not targets:
            raise ValueError(
                f"run_single: symbol={symbol} 不在 watchlist 中,跳過"
            )

        # 淺克隆 context.config 讓 watchlist 只剩目標股票,其他欄位不變
        from copy import copy
        from src.platform.runtime.config import AppConfig

        narrow_config = AppConfig(
            settings=context.config.settings,
            watchlist=targets,
        )
        narrow_context = copy(context)
        narrow_context.config = narrow_config
        return await self.run(narrow_context)

    # ---- 重寫 analyze:走 TradingAgents 多 Agent 流 ----

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        if not self._available:
            raise TradingAgentsUnavailable(self._import_error)

        stock = data["stock"]
        trace_id = getattr(context, "_trace_id", "") or self._make_trace_id(stock.symbol)
        force_refresh = bool(getattr(context, "_force_refresh", False))

        # 0) 同日快取命中(force_refresh=True 時跳過)
        if not force_refresh:
            cached = self._try_cache_hit(stock)
            if cached is not None:
                logger.info(
                    f"[TA] 命中同日快取 (agent=tradingagents symbol={stock.symbol})"
                )
                cached.raw_data["from_cache"] = True
                return cached

        # 1) 預算檢查
        budget = check_budget(self.monthly_budget_usd, self.name)
        if budget["exceeded"]:
            if self.over_budget_action == "reject":
                raise RuntimeError(
                    f"本月 TradingAgents 預算已用盡 "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f})。"
                    f"如需繼續使用,請在「設定」中調高預算上限。"
                )
            elif self.over_budget_action == "warn":
                logger.warning(
                    f"[TA] 預算已超,但策略=warn,繼續執行 "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f})"
                )

        # 2) 構造 TradingAgents config (支援 deep / quick 雙模型)
        from src.platform.persistence.database import DB_PATH
        ta_runtime_dir = Path(DB_PATH).resolve().parent / "tradingagents"
        ta_config = build_ta_llm_config(
            context.ai_client,
            debate_rounds=self.debate_rounds,
            selected_analysts=self.analyst_types,
            output_language=self.output_language,
            deep_model=self.deep_model,
            quick_model=self.quick_model,
            market=stock.market.value,
            enable_sec_edgar=self.enable_sec_edgar,
            runtime_dir=ta_runtime_dir,
            holding_period_days=self.holding_period_days,
            llm_timeout_seconds=self.llm_timeout_seconds,
            llm_max_retries=self.llm_max_retries,
            llm_max_tokens=self.llm_max_tokens,
        )

        # 3) 進度回撥
        progress_handler = getattr(context, "_progress_handler", None)
        if not isinstance(progress_handler, PanWatchProgressHandler):
            progress_handler = PanWatchProgressHandler(trace_id, self.name)
        cancel_event = threading.Event()
        progress_handler.cancel_event = cancel_event

        # 4) 標的元資訊走 instrument_context；使用者持倉走 TradingAgents 0.5.0 原生 portfolio。
        current_price = (data.get("quote") or {}).get("current_price")
        cur_price_num = current_price if isinstance(current_price, (int, float)) else None
        quote_data = data.get("quote") or {}

        meta_context = build_stock_metadata_context(
            stock_symbol=stock.symbol,
            stock_name=stock.name or "",
            market=stock.market.value,
            current_price=cur_price_num,
            industry=quote_data.get("industry", "") if isinstance(quote_data, dict) else "",
        )
        # 5) 同步阻塞,丟到執行緒池;加硬超時防卡死
        try:
            ta_result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_tradingagents_sync,
                    ai_client=context.ai_client,
                    symbol=stock.symbol,
                    market=stock.market.value,
                    ta_config=ta_config,
                    progress_handler=progress_handler,
                    panwatch_data=data,
                    stock_metadata_context=meta_context,
                    portfolio=getattr(context, "portfolio", None),
                    cancel_event=cancel_event,
                ),
                timeout=self.timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            cancel_event.set()
            # 超時:嘗試落庫部分進度供後續檢視
            partial_cost = getattr(progress_handler, "_total_cost", 0.0)
            partial_stages = list(getattr(progress_handler, "_completed_stages", set()))
            logger.warning(
                f"[TA] 執行超時 (>{self.timeout_minutes} 分鐘). "
                f"已完成階段: {partial_stages}, 累計成本 ${partial_cost:.4f}"
            )
            partial_msg = (
                f"分析超時(>{self.timeout_minutes} 分鐘)。"
                f"已完成 {len(partial_stages)} 個階段,累計成本 ${partial_cost:.4f}。"
                f"建議:① 縮短 debate_rounds;② 換更快的模型(如 deepseek-chat);"
                f"③ 調高 timeout_minutes。"
            )
            raise RuntimeError(partial_msg)
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        except Exception:
            cancel_event.set()
            raise

        # 5) 對映成 AnalysisResult
        result = map_state_to_result(
            stock=stock,
            ta_result=ta_result,
            model_label=context.model_label,
        )

        # 存分析時即時價 → 歷史決策表"分析價"立即顯示(不必等當日 K線收盤迴填)
        _quote = data.get("quote") or {}
        _cp = _quote.get("current_price")
        if isinstance(_cp, (int, float)):
            result.raw_data["price_at_analysis"] = float(_cp)

        # 5b) 把本次 trace_id 的 toolkit 診斷聚合,持久化進 raw_data,
        # 讓歷史報告 DoneView 也能展示資料注入情況。
        try:
            tid = getattr(progress_handler, "trace_id", "") if progress_handler else ""
            if tid:
                result.raw_data["toolkit_diagnostic"] = self._collect_toolkit_diagnostic(tid)
        except Exception as e:
            logger.warning(f"[TA] 收集 toolkit 診斷失敗,忽略: {e}")

        # 6) 落庫到 AnalysisHistory:供 UI 查最近一次結果 (DeepAnalysisModal 彈跳視窗) +
        # 月度成本預算聚合。同標的同日復跑會覆蓋 (analysis_history.save_analysis 語義)。
        try:
            save_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                content=result.content,
                title=result.title,
                raw_data=result.raw_data,
            )
        except Exception as e:
            logger.warning(f"[TA] save_analysis 失敗,不影響主流程: {e}")

        # 6b) 落庫到 StockSuggestion(建議池) — 讓持倉頁/關注列表上的建議徽章
        # 顯示 TradingAgents 的 BUY/HOLD/SELL 決策(跟「盤前分析」「收盤覆盤」並列)。
        try:
            from src.modules.automation.suggestion_pool import save_suggestion

            sug = result.raw_data.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "持有"
            signal_text = (sug.get("signal") or "")[:500]
            reason_text = (sug.get("reason") or "")[:1000]
            confidence = sug.get("confidence")
            confidence_text = (
                f" (置信度 {confidence:.1f}/10)" if isinstance(confidence, (int, float)) else ""
            )

            save_suggestion(
                stock_symbol=stock.symbol,
                stock_name=stock.name,
                stock_market=stock.market.value,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name=self.name,
                agent_label="TradingAgents 深度",
                signal=signal_text,
                reason=reason_text,
                expires_hours=24,  # 深度分析結果 24 小時內有效
                ai_response=result.content[:2000],
                meta={
                    "cost_usd": result.raw_data.get("cost_usd", 0),
                    "decision": result.raw_data.get("decision", "HOLD"),
                    "confidence": confidence,
                },
            )
        except Exception as e:
            logger.warning(f"[TA] save_suggestion 失敗,不影響主流程: {e}")

        # 7) 可選:把 BUY/SELL 決策寫入 StrategySignalRun 驅動模擬交易
        if self.emit_paper_trading_signal:
            try:
                from src.modules.automation.tradingagents.decision import (
                    maybe_emit_paper_trading_signal,
                )
                quote = data.get("quote") or {}
                current_price = quote.get("current_price")
                sug = result.raw_data.get("suggestion") or {}
                maybe_emit_paper_trading_signal(
                    stock_symbol=stock.symbol,
                    stock_market=stock.market.value,
                    stock_name=stock.name,
                    decision=str(sug.get("action") or ""),
                    confidence=float(sug.get("confidence") or 5.0),
                    signal_text=str(sug.get("signal") or ""),
                    reason=str(sug.get("reason") or ""),
                    current_price=current_price,
                    enabled=True,
                )
            except Exception as e:
                logger.warning(f"[TA] 寫模擬交易訊號失敗,不影響主流程: {e}")

        return result

    # ---- 私有方法 ----

    def _check_availability(self) -> tuple[bool, str]:
        """檢測 tradingagents 是否可用。"""
        try:
            import tradingagents  # noqa: F401
            from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
        except ImportError as e:
            return False, (
                "tradingagents 未安裝。執行 `pip install -r requirements.txt` "
                "(或單獨 `pip install \"tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git\"`)。"
                "公司代理下若失敗,臨時 `env -u HTTP_PROXY -u HTTPS_PROXY pip install -r requirements.txt`。"
                f"原始錯誤: {e}"
            )
        except Exception as e:
            return False, f"tradingagents 載入失敗: {e}"
        return True, ""

    def _make_trace_id(self, symbol: str) -> str:
        return f"ta-{symbol}-{int(datetime.now().timestamp())}"

    def _try_cache_hit(self, stock) -> AnalysisResult | None:
        """同標的同日是否已分析過 → 返回快取的 AnalysisResult。"""
        if self.cache_ttl_hours <= 0:
            return None
        try:
            history = get_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                analysis_date=date.today(),
            )
        except Exception:
            return None
        if not history or not history.raw_data:
            return None
        return AnalysisResult(
            agent_name=self.name,
            title=history.title or f"【深度·快取】{stock.name}({stock.symbol})",
            content=history.content,
            raw_data=dict(history.raw_data),
        )

    def _run_tradingagents_sync(
        self,
        *,
        ai_client,
        symbol: str,
        market: str,
        ta_config: dict,
        progress_handler,
        panwatch_data: dict,
        stock_metadata_context: str = "",
        portfolio: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """在 worker 執行緒跑同步 TradingAgents 流程。

        步驟:
        1. inject_api_key_env 注入 API key 到環境變數
        2. patch_route_to_vendor 讓 A 股請求路由到 PanWatch 資料
        3. TradingAgentsGraph.propagate 跑 3-5 分鐘
        4. 返回 decision + final_state + cost_usd
        """
        # 關鍵依賴延遲 import,確保 _check_availability 失敗時這裡不被呼叫
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # 應用 LangChain 相容性補丁:讓小模型 (Qwen 7B 等) 返回的
        # tool_calls.args 字串被自動轉 dict。
        apply_compat_patches()
        inject_api_key_env(ai_client)

        # patch + 資料上下文,確保 TradingAgents 調 route_to_vendor 時拿到 PanWatch 資料
        trace_id_for_ctx = getattr(progress_handler, "trace_id", "") if progress_handler else ""
        with patch_route_to_vendor(), panwatch_data_context(
            panwatch_data,
            trace_id=trace_id_for_ctx,
            cancel_event=cancel_event,
        ):
            graph = _bounded_graph_class(TradingAgentsGraph)(
                selected_analysts=ta_config["selected_analysts"],
                debug=False,
                config=ta_config,
                # callbacks 接受 langchain BaseCallbackHandler 列表;LLM 級別用
                callbacks=[progress_handler] if progress_handler else None,
            )

            # 注入 LangGraph 節點級 callbacks(propagator.get_graph_args 預設 callbacks=None,
            # 不會觸發 on_chain_start/end → 進度條永遠卡 pending)
            if progress_handler is not None:
                self._inject_graph_callbacks(graph, progress_handler)

            # 0.5.0 原生提供 instrument_context；標的後設資料不應汙染 past_context。
            if stock_metadata_context:
                patch_instrument_context(graph, stock_metadata_context)

            date_str = datetime.now().strftime("%Y-%m-%d")
            final_state, decision = graph.propagate(
                symbol,
                date_str,
                portfolio=to_tradingagents_portfolio(portfolio),
            )

        # 成本提取(TradingAgents 內部 token 統計;若上游未暴露,fallback 用 estimate)
        cost_usd = self._extract_cost_from_graph(graph) or self._fallback_cost_estimate(
            ta_config
        )

        return {
            "decision": str(decision or "HOLD").upper(),
            "final_state": dict(final_state) if final_state else {},
            "cost_usd": float(cost_usd or 0.0),
        }

    @staticmethod
    def _inject_graph_callbacks(graph, handler):
        """Monkey-patch graph.propagator.get_graph_args 讓 LangGraph 節點級 callbacks 也注入。

        否則只有 on_llm_start/end 會觸發,on_chain_start/end (節點切換) 不會,進度條卡死。
        """
        try:
            propagator = getattr(graph, "propagator", None)
            if propagator is None or not hasattr(propagator, "get_graph_args"):
                return
            original = propagator.get_graph_args

            def _patched(callbacks=None):
                cbs = list(callbacks or [])
                if handler not in cbs:
                    cbs.append(handler)
                return original(callbacks=cbs)

            propagator.get_graph_args = _patched  # type: ignore[method-assign]
        except Exception as e:
            logger.warning(f"[TA] 注入 LangGraph callbacks 失敗: {e}")

    @staticmethod
    def _collect_toolkit_diagnostic(trace_id: str) -> dict:
        """查同 trace_id 的 ta_toolkit 日誌,聚合成 {summary, recent}。"""
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import LogEntry

        db = SessionLocal()
        try:
            rows = (
                db.query(LogEntry)
                .filter(LogEntry.trace_id == trace_id, LogEntry.event == "ta_toolkit")
                .order_by(LogEntry.id.asc())
                .all()
            )
        finally:
            db.close()

        summary = {"hit": 0, "miss": 0, "passthrough": 0, "fallthrough": 0, "error": 0}
        recent = []
        for r in rows:
            tags = r.tags or {}
            action = (tags.get("action") or "").lower()
            if action in summary:
                summary[action] += 1
            recent.append({
                "action": tags.get("action"),
                "method": tags.get("method"),
                "symbol": tags.get("symbol"),
                "chars": tags.get("chars"),
                "snippet": tags.get("snippet"),
                "source": tags.get("source"),
                "reason": tags.get("reason"),
            })
        return {"summary": summary, "recent": recent[-50:]}

    @staticmethod
    def _extract_cost_from_graph(graph) -> float:
        """嘗試從 TradingAgentsGraph 例項提取累計成本。上游未必暴露欄位,容錯。"""
        for attr in ("total_cost", "total_cost_usd", "_total_cost"):
            v = getattr(graph, attr, None)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _fallback_cost_estimate(self, ta_config: dict) -> float:
        """fallback 用 estimate 平均值。"""
        est = estimate_cost(
            debate_rounds=ta_config.get("max_debate_rounds", 1),
            selected_analysts=ta_config.get("selected_analysts", []),
            model=ta_config.get("deep_think_llm", "deepseek-chat"),
        )
        return (est["cost_low_usd"] + est["cost_high_usd"]) / 2
