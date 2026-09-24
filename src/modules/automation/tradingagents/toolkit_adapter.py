"""把 PanWatch Provider 體系適配進 TradingAgents 資料流。

TradingAgents 上游(0.2.x)預設透過 `tradingagents.dataflows.interface.route_to_vendor`
把資料請求路由到 yfinance / alpha_vantage 等 vendor。**沒有公開 toolkit 注入入口**。

我們的策略:**monkeypatch route_to_vendor**。當 LangGraph 節點呼叫 `get_stockstats_*`
等方法時,我們的 patch 檢測 symbol 是 A 股程式碼(6 位數字)就走 PanWatch Provider,
否則放行到上游預設 vendor(yfinance 等)。

這避免:
- TradingAgents 用 yfinance 拉 A 股拉不到(A 股 yfinance 不全)
- 重複請求外部 API(PanWatch 已有快取的 quote/kline 直接複用)

也保留:
- US/HK 走上游 yfinance vendor 不變
- 使用者可關閉 patch 走原生路徑

注意:本模組對上游 TradingAgents API 有強依賴,如上游重構 route_to_vendor 介面
需要同步更新。已透過 `tradingagents` 軟依賴 + try/except 優雅降級。
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


# 快取:在 patch 上下文裡把 PanWatch 拉好的資料塞這裡,patch 命中時直接返回。
# 用 ContextVar 而非模組級 dict:深度分析跑在 asyncio.to_thread worker 執行緒,
# to_thread 會 copy_context(),每個併發任務拿到獨立副本 —— 避免兩隻標的併發
# 分析時互相覆蓋資料(廣汽 601238 的報告混入賽力斯 601127 的 K線/新聞)。
_PANWATCH_DATA: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_TA_PANWATCH_DATA", default={}
)

# 跟隨當前請求的 trace_id;toolkit hit/miss 日誌歸屬到這次分析
_CURRENT_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_TA_TRACE_ID", default=""
)
_CANCEL_EVENT: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "_TA_CANCEL_EVENT", default=None
)

# 所有上游 monkeypatch 和 PanWatch 資料注入都集中在本檔案；其它模組只依賴這些入口。
__all__ = [
    "TradingAgentsCancelled",
    "hk_symbol_to_yfinance",
    "is_a_share",
    "is_hk_share",
    "is_panwatch_routable",
    "panwatch_data_context",
    "patch_route_to_vendor",
]


class TradingAgentsCancelled(RuntimeError):
    """TradingAgents 任務已進入終態，禁止殘留 worker 再發起外部請求。"""


def _raise_if_cancelled() -> None:
    event = _CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise TradingAgentsCancelled("TradingAgents task cancelled")


def _cache() -> dict[str, Any]:
    """讀當前 context 的 PanWatch 資料快照(併發隔離)。"""
    return _PANWATCH_DATA.get()


@contextmanager
def panwatch_data_context(
    data: dict[str, Any],
    trace_id: str = "",
    cancel_event: threading.Event | None = None,
):
    """在呼叫 TradingAgents 的程式碼塊周圍用本 context manager 注入資料。

    Args:
        data: 含 stock / quote / klines / events / capital_flow 的字典
        trace_id: 本次分析的 trace_id,用於把 toolkit 命中日誌歸屬到該執行

    退出 context 時還原資料。基於 ContextVar,併發任務(及其 to_thread worker)
    互不幹擾。
    """
    token = _PANWATCH_DATA.set(dict(data))
    tid_token = _CURRENT_TRACE_ID.set(trace_id or "")
    cancel_token = _CANCEL_EVENT.set(cancel_event)
    try:
        yield
    finally:
        _CANCEL_EVENT.reset(cancel_token)
        _PANWATCH_DATA.reset(token)
        _CURRENT_TRACE_ID.reset(tid_token)


def _emit_toolkit_log(level: str, action: str, method_name: str, symbol: str, **extra):
    """把 toolkit hit/miss/passthrough 寫進同 trace_id 的日誌,前端可在彈跳視窗看到。"""
    from src.platform.observability.log_context import log_context

    trace_id = _CURRENT_TRACE_ID.get()
    if not trace_id:
        # 沒 trace_id 也打普通日誌(可在日誌中心按 logger 過濾)
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")
        return
    with log_context(
        trace_id=trace_id,
        agent_name="tradingagents",
        event="ta_toolkit",
        tags={"action": action, "method": method_name, "symbol": symbol, **extra},
    ):
        getattr(logger, level)(f"[TA toolkit] {action} method={method_name} symbol={symbol} {extra}")


def is_a_share(symbol: str) -> bool:
    """A 股程式碼判定:6 位純數字。"""
    return bool(symbol) and len(symbol) == 6 and symbol.isdigit()


def is_hk_share(symbol: str) -> bool:
    """港股程式碼判定:5 位純數字(00241/00700/...)。"""
    return bool(symbol) and len(symbol) == 5 and symbol.isdigit()


def is_panwatch_routable(symbol: str) -> bool:
    """該 ticker 是否應該走 PanWatch 資料(而不是上游 yfinance)。

    A 股(6 位數字)yfinance 拉不到,港股(5 位數字)yfinance 也要 .HK 字尾,
    都需要 PanWatch 兜底。美股(字母 ticker)繼續走 yfinance。
    """
    return is_a_share(symbol) or is_hk_share(symbol)


def _looks_like_cn_keyword(symbol: str) -> bool:
    """含中文字元 = 行業/主題中文檢索詞(如「汽車行業」)→ 走東財關鍵詞新聞;
    純字母 ticker(美股 BABA/NVDA 等)不算 → 應透傳上游 Yahoo 個股新聞。"""
    return any("一" <= ch <= "鿿" for ch in str(symbol or ""))


def hk_symbol_to_yfinance(symbol: str) -> str:
    """港股 PanWatch 5 位程式碼 → yfinance 格式。

    阿里健康 00241 → 0241.HK
    騰訊 00700 → 0700.HK
    yfinance 港股是 4 位數字 + .HK 字尾。
    """
    if not is_hk_share(symbol):
        return symbol
    # 去掉首位 0(00241 → 0241),保留 4 位
    s = symbol.lstrip("0")
    if len(s) > 4:
        s = s[-4:]
    return s.zfill(4) + ".HK"


def _yfinance_response_has_data(text: str) -> bool:
    """啟發式判斷 yfinance 返回是否包含真實資料。

    yfinance 拿不到資料時返回類似:"No data found for symbol 'XXX' between ..."
    或返回極短的空表頭。
    """
    if not text:
        return False
    t = str(text).strip()
    if len(t) < 50:
        return False
    low = t.lower()
    if any(kw in low for kw in (
        "no data found",
        "no data available",
        "no_data_available",
        "no usable market data",
        "symbol may be delisted",
        "no information available",
    )):
        return False
    return True


# 上游 tool 檔案用 `from tradingagents.dataflows.interface import route_to_vendor`,
# 這是 import-time binding,每個模組持有 **原函式引用**。
# 只 patch 源頭模組屬性不夠 —— 必須把每個 import site 的 module-level
# 名字都替換掉,所有呼叫才會走我們的攔截。
_ROUTE_TO_VENDOR_IMPORT_SITES = (
    "tradingagents.agents.utils.fundamental_data_tools",
    "tradingagents.agents.utils.news_data_tools",
    "tradingagents.agents.utils.core_stock_tools",
    "tradingagents.agents.utils.technical_indicators_tools",
)


# patch 引用計數:多個併發深度分析共享同一次安裝,第一個進入者儲存真
# route_to_vendor 並裝到所有 import site,最後一個退出才恢復。資料隔離靠
# _PANWATCH_DATA(ContextVar),patch 本身只需程式級安裝一次 —— 消除原先
# "A 退出時把全域性恢復成 B 的 _patched"的巢狀競態。
_patch_lock = threading.Lock()
_patch_refcount = 0
_patch_saved_sites: list[tuple[Any, str, Any]] = []  # (module, attr_name, original_value)
_real_route_to_vendor = None  # 真 route_to_vendor(走上游 vendor 時用)


_DATE_ARGUMENT = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


def _looks_like_date(value: Any) -> bool:
    """判斷 route_to_vendor 的字串引數是不是日期，而不是用數字字首誤判 ticker。

    A/HK 股票程式碼本身就是純數字（如 300624、00700），因此不能再用
    ``value[:4].isdigit()`` 之類的啟發式過濾；只有明確匹配日期格式才跳過。
    """
    return isinstance(value, str) and bool(_DATE_ARGUMENT.fullmatch(value.strip()))


def _extract_requested_symbol(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """從上游工具引數提取 ticker，相容 get_global_news 的日期首參。"""
    for value in args:
        if isinstance(value, str) and value.strip() and not _looks_like_date(value):
            return value.strip()
    return str(kwargs.get("symbol") or kwargs.get("ticker") or "").strip()


def _cached_symbol() -> str:
    stock = _cache().get("stock")
    return str(getattr(stock, "symbol", "") or "").strip()


def _patched_route_to_vendor(method_name: str, *args, **kwargs):
    """模組級無狀態 patch:A 股走 PanWatch(讀 _cache()),港股先試上游再兜底,其餘放行。

    與上游 route_to_vendor(method, *args, **kwargs) 完全同簽名。上游所有 toolkit
    都用 positional 傳 ticker:
      route_to_vendor("get_fundamentals", ticker, curr_date)
      route_to_vendor("get_news", ticker, start_date, end_date)
      route_to_vendor("get_stock_data", symbol, ...)
      route_to_vendor("get_global_news", curr_date, look_back_days, limit)  # 無 symbol

    無任何例項狀態:symbol 來自呼叫引數,資料來自 _cache()(當前 context),
    所以多個併發任務共享同一個 _patched 也不會串臺。
    """
    _raise_if_cancelled()
    # 不過濾純數字：A/HK ticker 合法地由數字組成；僅跳過明確的日期引數。
    symbol = _extract_requested_symbol(args, kwargs)

    # 沒拿到 symbol 時(如 get_global_news),用 cache 裡的標的兜底,
    # 攔截"全域性新聞"類呼叫避免拉到無關 Yahoo 鞋類/汽油新聞。
    if not symbol:
        cached_symbol = _cached_symbol()
        if is_panwatch_routable(cached_symbol):
            symbol = cached_symbol

    # 工具請求了另一個 A/HK 標的時，禁止拿當前任務的快照冒充它。
    # 這條邊界比“儘量返回資料”更重要：錯誤標的資料會讓後續 LLM 生成看似完整但完全錯誤的報告。
    cached_symbol = _cached_symbol()
    snapshot_symbol_mismatch = bool(
        symbol
        and is_panwatch_routable(symbol)
        and cached_symbol
        and symbol != cached_symbol
        and _cache()
    )
    if snapshot_symbol_mismatch and is_a_share(symbol):
        message = _data_unavailable_message(
            method_name,
            symbol,
            RuntimeError(f"PanWatch snapshot is for {cached_symbol}, not {symbol}"),
        )
        _emit_toolkit_log(
            "warning",
            "DEGRADE",
            method_name,
            symbol,
            reason=f"snapshot symbol mismatch: cached={cached_symbol}",
            extra_args=_args_summary(args),
        )
        return message

    # A 股:yfinance/finnhub 拉不到,直接走 PanWatch
    if is_a_share(symbol) and _cache():
        try:
            result = _serve_from_panwatch(method_name, symbol, kwargs, args=args)
            _emit_toolkit_log(
                "info", "HIT", method_name, symbol,
                chars=len(result),
                snippet=str(result)[:4000],
                source="panwatch",
                extra_args=_args_summary(args),
            )
            return result
        except NotImplementedError:
            _emit_toolkit_log(
                "info", "MISS", method_name, symbol,
                reason="PanWatch 未實現該 method,放行到上游",
            )
        except Exception as e:
            _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
            return f"[PanWatch error: {e}]"

    # 港股:先把 ticker 轉成 yfinance 格式(00241 → 0241.HK)試上游,
    # yfinance 返回有資料就用,無資料(No data found / 極短返回)fallback 到 PanWatch。
    if is_hk_share(symbol):
        yf_symbol = hk_symbol_to_yfinance(symbol)
        new_args = list(args)
        # 替換第一個 positional ticker(如果它就是當前 symbol)
        for i, a in enumerate(new_args):
            if isinstance(a, str) and a == symbol:
                new_args[i] = yf_symbol
                break
        try:
            upstream_result = _real_route_to_vendor(method_name, *new_args, **kwargs)
        except Exception as e:
            if not _is_market_data_failure(e):
                raise
            upstream_result = ""
            logger.warning(f"[TA toolkit] HK upstream {method_name}({yf_symbol}) 失敗: {e}")
        upstream_str = str(upstream_result) if upstream_result is not None else ""

        if _yfinance_response_has_data(upstream_str):
            # 走上游 vendor 拿到資料 = PASSTHROUGH,只是 source 標記轉格式
            _emit_toolkit_log(
                "info", "PASSTHROUGH", method_name, symbol,
                chars=len(upstream_str),
                snippet=upstream_str[:4000],
                source=f"upstream HK(→{yf_symbol})",
                extra_args=_args_summary(args),
            )
            return upstream_result

        # yfinance 沒資料 → fallback 到 PanWatch = HIT(PanWatch 兜底提供資料)
        if _cache() and not snapshot_symbol_mismatch:
            try:
                result = _serve_from_panwatch(method_name, symbol, kwargs, args=args)
                _emit_toolkit_log(
                    "info", "HIT", method_name, symbol,
                    chars=len(result),
                    snippet=str(result)[:4000],
                    source="panwatch HK fallback",
                    extra_args=_args_summary(args),
                )
                return result
            except NotImplementedError:
                pass
            except Exception as e:
                _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
                return f"[PanWatch error: {e}]"
        # 港股兩邊都沒 = ERROR
        _emit_toolkit_log(
            "warning", "ERROR", method_name, symbol,
            chars=len(upstream_str), snippet=upstream_str[:4000],
            source=f"upstream HK(→{yf_symbol}) + panwatch 均空",
            error="HK no data from either source",
        )
        return upstream_result

    # 行業/主題新聞:get_news 的 query 不是 ticker(中文行業詞等) → 即時搜中文新聞(東方財富),
    # 替代拉不到中文資料的上游 vendor。
    if symbol and "news" in method_name.lower() and not is_panwatch_routable(symbol) and _looks_like_cn_keyword(symbol):
        try:
            result = _serve_keyword_news(symbol)
            _emit_toolkit_log(
                "info", "HIT", method_name, symbol,
                chars=len(result), snippet=result[:4000],
                source="panwatch keyword news", extra_args=_args_summary(args),
            )
            return result
        except Exception as e:
            _emit_toolkit_log("warning", "ERROR", method_name, symbol, error=str(e)[:200])
            return f"[關鍵詞新聞搜尋失敗「{symbol}」: {e}]"

    # 美股 / 其他:直接走上游 vendor。
    # 降級兜底:上游某些工具依賴外部 key/服務(FRED 無 key、polymarket SSL、未配置 vendor 等),
    # 失敗會拋異常拖垮整個深度分析。這裡捕獲並返回空 —— 單個工具缺資料 ≠ 整輪失敗。
    try:
        upstream_result = _real_route_to_vendor(method_name, *args, **kwargs)
    except Exception as e:
        if not _is_market_data_failure(e):
            raise
        result = _data_unavailable_message(method_name, symbol, e)
        logger.warning(f"[TA toolkit] 上游 {method_name} 資料不可用: {e}")
        _emit_toolkit_log(
            "warning", "DEGRADE", method_name, symbol or "(none)",
            error=str(e)[:200], extra_args=_args_summary(args),
        )
        return result
    upstream_str = str(upstream_result) if upstream_result is not None else ""
    action_label = "PASSTHROUGH" if not is_a_share(symbol) else "FALLTHROUGH"
    _emit_toolkit_log(
        "info", action_label, method_name, symbol or "(none)",
        chars=len(upstream_str),
        snippet=upstream_str[:4000],
        source="upstream",
        extra_args=_args_summary(args),
    )
    return upstream_result


@contextmanager
def patch_route_to_vendor():
    """Monkeypatch tradingagents.dataflows.interface.route_to_vendor + 所有 import sites。

    當請求 A 股程式碼時,從 _PANWATCH_DATA(當前 context)返回 PanWatch 已拉的資料。
    非 A 股放行到原函式。

    引用計數 + 鎖:併發的多個深度分析共享同一次安裝,第一個進入者裝、最後一個
    退出才解除安裝,_real_route_to_vendor 永遠儲存真函式 —— 消除巢狀 patch 鏈錯亂。

    如果 tradingagents 庫未安裝,本 context manager 是 no-op,不拋異常。
    """
    global _patch_refcount, _real_route_to_vendor

    try:
        from tradingagents.dataflows import interface as ta_interface
    except ImportError:
        logger.warning("[TA toolkit] tradingagents 未安裝,跳過 monkeypatch")
        yield
        return

    if not hasattr(ta_interface, "route_to_vendor"):
        logger.warning(
            "[TA toolkit] route_to_vendor 不存在 (上游 API 可能變更),"
            "走預設 vendor 路徑"
        )
        yield
        return

    # 同時接管 load_ohlcv:新上游 get_verified_market_snapshot 繞過 route_to_vendor。
    _ensure_load_ohlcv_patched()
    _ensure_market_snapshot_patched()

    import importlib
    with _patch_lock:
        if _patch_refcount == 0:
            # 第一個進入者:儲存真函式並裝到源頭 + 所有 import sites
            # (`from ... import route_to_vendor` 是 import-time binding,只 patch
            # 源頭不夠 — 工具模組持有的原引用不變)
            _real_route_to_vendor = ta_interface.route_to_vendor
            _patch_saved_sites.clear()
            ta_interface.route_to_vendor = _patched_route_to_vendor
            _patch_saved_sites.append((ta_interface, "route_to_vendor", _real_route_to_vendor))
            for module_path in _ROUTE_TO_VENDOR_IMPORT_SITES:
                try:
                    mod = importlib.import_module(module_path)
                except ImportError:
                    continue
                if hasattr(mod, "route_to_vendor"):
                    _patch_saved_sites.append((mod, "route_to_vendor", mod.route_to_vendor))
                    mod.route_to_vendor = _patched_route_to_vendor
                    logger.debug(f"[TA toolkit] patched route_to_vendor in {module_path}")
        _patch_refcount += 1

    try:
        yield
    finally:
        with _patch_lock:
            _patch_refcount -= 1
            if _patch_refcount <= 0:
                _patch_refcount = 0
                for mod, attr, orig in _patch_saved_sites:
                    setattr(mod, attr, orig)
                _patch_saved_sites.clear()


# ---------------------------------------------------------------------------
# load_ohlcv 接管
# 新上游 get_verified_market_snapshot → market_data_validator.load_ohlcv 直連 yfinance,
# 不經 route_to_vendor。A股(無 .SS)/港股(無 .HK)yfinance 拉不到 → NoMarketDataError,
# 整個 TradingAgents 分析失敗。這裡把 A股/港股的 load_ohlcv 改走 PanWatch K線;
# 非 PanWatch 標的(美股)透傳原生 yfinance,故程式級永久安裝安全、無需解除安裝。
# ---------------------------------------------------------------------------
_LOAD_OHLCV_PATCHED = False
_real_load_ohlcv: Any = None
_LOAD_OHLCV_IMPORT_SITES = (
    "tradingagents.dataflows.market_data_validator",
    "tradingagents.dataflows.interface",
    "tradingagents.dataflows.y_finance",
)

_MARKET_SNAPSHOT_PATCHED = False
_real_build_verified_market_snapshot: Any = None
_MARKET_SNAPSHOT_IMPORT_SITES = (
    "tradingagents.agents.utils.market_data_validation_tools",
)


def _market_for_symbol(symbol: str):
    """將 TradingAgents 的 ticker 對映到 PanWatch 市場。"""
    from src.platform.marketdata.models import MarketCode

    if is_a_share(symbol):
        return MarketCode.CN
    if is_hk_share(symbol):
        return MarketCode.HK
    return MarketCode.US


def _build_panwatch_ohlcv_df(symbol: str, curr_date: str):
    """用 PanWatch K線構建與原生 load_ohlcv 同結構的 DataFrame(Date/Open/High/Low/Close/Volume)。"""
    _raise_if_cancelled()
    import pandas as pd

    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    market = _market_for_symbol(symbol)
    # collect() 已經為本次分析準備了 K 線；驗證快照只需要同一份資料，
    # 不應因為上游預設 lookback=750 再向東財發起一輪可能阻塞的請求。
    cached_klines = _cache().get("klines")
    cached_stock = _cache().get("stock")
    cached_symbol = getattr(cached_stock, "symbol", "") if cached_stock is not None else ""
    cache_matches_symbol = bool(cached_symbol) and str(cached_symbol) == str(symbol)
    if cache_matches_symbol and isinstance(cached_klines, (list, tuple)):
        klines = list(cached_klines)
    else:
        klines = KlineCollector(market).get_klines(symbol, days=750)
    if not klines:
        return None
    df = pd.DataFrame(
        [
            {
                "Date": k.date,
                "Open": k.open,
                "High": k.high,
                "Low": k.low,
                "Close": k.close,
                "Volume": k.volume,
            }
            for k in klines
        ]
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    if curr_date:
        try:
            df = df[df["Date"] <= pd.to_datetime(curr_date)]
        except Exception:
            pass
    return df.reset_index(drop=True)


def _is_market_data_failure(error: Exception) -> bool:
    """判斷異常是否表示外部行情不可用，而非程式自身錯誤。"""
    name = type(error).__name__.lower()
    detail = str(error).lower()
    return (
        name in {
            "yfratelimiterror",
            "nomarketdataerror",
            "vendorratelimiterror",
            "vendornotconfigurederror",
        }
        or any(token in detail for token in (
            "api_key",
            "api key",
            "not configured",
            "too many requests",
            "rate limited",
            "no market data",
            "no price data",
            "no ohlcv data",
            "yahoo finance returned no rows",
            "no timezone found",
            "possibly delisted",
            "connection",
            "timeout",
            "service unavailable",
            "server error",
            "http error",
        ))
    )


def _data_unavailable_message(method_name: str, symbol: str, error: Exception) -> str:
    """給上游 agent 的顯式降級結果，禁止將不可用資料預設為中性資料。"""
    return (
        "DATA_UNAVAILABLE: "
        f"method={method_name}; symbol={symbol or 'N/A'}; reason={str(error)[:300]}. "
        "Do not infer missing values or treat this as neutral evidence. "
        "State the data limitation and request manual review when it affects the decision."
    )


def _load_panwatch_ohlcv_or_raise(symbol: str, curr_date: str, *, fallback: bool = False):
    """讀取 MarketData 的 K 線；沒有可驗證的 OHLCV 時丟擲統一的資料錯誤。"""
    df = None
    try:
        df = _build_panwatch_ohlcv_df(symbol, curr_date)
    except Exception as exc:
        logger.warning(f"[TA toolkit] load_ohlcv MarketData 取數異常 symbol={symbol}: {exc}")
    if df is not None and not df.empty:
        action = "FALLBACK" if fallback else "HIT"
        _emit_toolkit_log(
            "info", action, "load_ohlcv", symbol, rows=int(len(df)), source="marketdata"
        )
        return df

    _emit_toolkit_log("warning", "MISS", "load_ohlcv", symbol, source="marketdata")
    try:
        from tradingagents.dataflows.errors import NoMarketDataError
        raise NoMarketDataError(
            symbol, symbol,
            "MarketData K線獲取失敗，請檢查資料來源、代理分流或稍後重試",
        )
    except ImportError:
        raise RuntimeError(
            f"MarketData K線獲取失敗 symbol={symbol}(請檢查資料來源或代理分流)"
        )


def _panwatch_load_ohlcv(symbol: str, curr_date: str, *args, **kwargs):
    """A/HK 直接走 MarketData；美股優先 Yahoo，失敗時再降級 MarketData。"""
    _raise_if_cancelled()
    if is_panwatch_routable(symbol):
        return _load_panwatch_ohlcv_or_raise(symbol, curr_date)

    try:
        upstream_df = _real_load_ohlcv(symbol, curr_date, *args, **kwargs)
        if upstream_df is not None and not upstream_df.empty:
            _emit_toolkit_log("info", "PASSTHROUGH", "load_ohlcv", symbol, source="yfinance")
            return upstream_df
        logger.warning(f"[TA toolkit] Yahoo OHLCV 為空，降級 MarketData symbol={symbol}")
    except Exception as exc:
        if not _is_market_data_failure(exc):
            raise
        logger.warning(f"[TA toolkit] Yahoo OHLCV 不可用，降級 MarketData symbol={symbol}: {exc}")
        _emit_toolkit_log("warning", "DEGRADE", "load_ohlcv", symbol, source="yfinance", error=str(exc)[:200])
    _raise_if_cancelled()
    return _load_panwatch_ohlcv_or_raise(symbol, curr_date, fallback=True)


def _safe_build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Any = None,
) -> str:
    """行情全部不可用時返回約束性提示，避免單個工具異常中斷圖執行。"""
    try:
        return _real_build_verified_market_snapshot(
            symbol, curr_date, look_back_days, indicators=indicators
        )
    except Exception as exc:
        if not _is_market_data_failure(exc):
            raise
        _emit_toolkit_log(
            "warning", "DEGRADE", "get_verified_market_snapshot", symbol,
            source="all market data sources", error=str(exc)[:200],
        )
        return (
            f"## Verified market data unavailable for {symbol.upper()}\n\n"
            f"- Requested analysis date: {curr_date}\n"
            "- No usable OHLCV data was returned by the configured providers.\n\n"
            "Do not make exact price, indicator, stop-loss, or trade-action claims. "
            "State that market data is temporarily unavailable and limit the analysis "
            "to non-price qualitative context."
        )


def _ensure_load_ohlcv_patched() -> None:
    """程式級冪等安裝 load_ohlcv 補丁（A/HK 走 MarketData，美股可降級）。"""
    global _LOAD_OHLCV_PATCHED, _real_load_ohlcv
    if _LOAD_OHLCV_PATCHED:
        return
    try:
        from tradingagents.dataflows import stockstats_utils
    except ImportError:
        return
    if not hasattr(stockstats_utils, "load_ohlcv"):
        return
    import importlib

    with _patch_lock:
        if _LOAD_OHLCV_PATCHED:
            return
        _real_load_ohlcv = stockstats_utils.load_ohlcv
        stockstats_utils.load_ohlcv = _panwatch_load_ohlcv
        for module_path in _LOAD_OHLCV_IMPORT_SITES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            if getattr(mod, "load_ohlcv", None) is not None:
                mod.load_ohlcv = _panwatch_load_ohlcv
                logger.debug(f"[TA toolkit] patched load_ohlcv in {module_path}")
        _LOAD_OHLCV_PATCHED = True
        logger.info("[TA toolkit] load_ohlcv 已接管(A/HK走MarketData，美股Yahoo失敗時降級)")


def _ensure_market_snapshot_patched() -> None:
    """將驗證快照改為行情全失敗時返回安全提示，而不是讓圖執行失敗。"""
    global _MARKET_SNAPSHOT_PATCHED, _real_build_verified_market_snapshot
    if _MARKET_SNAPSHOT_PATCHED:
        return
    try:
        from tradingagents.dataflows import market_data_validator
    except ImportError:
        return
    if not hasattr(market_data_validator, "build_verified_market_snapshot"):
        return
    import importlib

    with _patch_lock:
        if _MARKET_SNAPSHOT_PATCHED:
            return
        _real_build_verified_market_snapshot = market_data_validator.build_verified_market_snapshot
        market_data_validator.build_verified_market_snapshot = _safe_build_verified_market_snapshot
        for module_path in _MARKET_SNAPSHOT_IMPORT_SITES:
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            if getattr(mod, "build_verified_market_snapshot", None) is not None:
                mod.build_verified_market_snapshot = _safe_build_verified_market_snapshot
                logger.debug(f"[TA toolkit] patched build_verified_market_snapshot in {module_path}")
        _MARKET_SNAPSHOT_PATCHED = True
        logger.info("[TA toolkit] 已為驗證行情快照安裝安全降級")


def _args_summary(args: tuple) -> str:
    """把 positional args 簡短列印,放進日誌 extra_args 便於區分 get_indicators 多次呼叫。

    e.g. ("601238", "macd", "2026-05-17", 30) → "macd, 2026-05-17, 30"(跳過 symbol)
    """
    if not args:
        return ""
    parts = []
    for i, a in enumerate(args):
        if i == 0 and isinstance(a, str) and len(a) == 6 and a.isdigit():
            continue  # 跳過 symbol(已單獨顯示)
        s = str(a)
        if len(s) > 40:
            s = s[:40] + "..."
        parts.append(s)
    return ", ".join(parts)


def _stock_meta_header(symbol: str) -> str:
    """渲染標的元資訊(公司名/市場/價格),作為所有工具返回的字首。

    A 股 ticker 不在 yfinance/finnhub 資料集,LLM 不能從 ticker 反查公司名,
    必須顯式告訴它"601127 = 賽力斯",否則會瞎編(如把 601127 當中國平安)。
    """
    stock = _cache().get("stock")
    quote = _cache().get("quote") or {}

    name = ""
    market = "CN"
    industry = ""
    if stock is not None:
        name = getattr(stock, "name", "") or ""
        market_obj = getattr(stock, "market", None)
        market = getattr(market_obj, "value", str(market_obj or "CN"))
    if not name and isinstance(quote, dict):
        name = quote.get("name") or ""
    if isinstance(quote, dict):
        industry = quote.get("industry") or ""

    market_label = {"CN": "中國 A 股", "HK": "港股", "US": "美股"}.get(market, market)
    cur_price = _attr(quote, "current_price", "") or _attr(quote, "price", "")
    change_pct = _attr(quote, "change_pct", "")

    lines = [
        f"[Stock Metadata] symbol={symbol}, name={name or 'N/A'}, market={market_label}",
    ]
    if industry:
        lines.append(f"  Industry: {industry}")
    if cur_price:
        try:
            lines.append(
                f"  Current price: {float(cur_price):.2f}"
                + (f" ({float(change_pct):+.2f}%)" if change_pct != "" else "")
            )
        except (TypeError, ValueError):
            pass
    lines.append(
        "  IMPORTANT: This is an A-share / HK / cross-market ticker. DO NOT guess the company "
        "from the ticker code alone — use the name above."
    )
    return "\n".join(lines)


def _serve_from_panwatch(method_name: str, symbol: str, kwargs: dict, args: tuple = ()) -> str:
    """從 _cache()(當前 context 的資料)構造 TradingAgents 期望的資料格式(CSV / JSON 字串)。

    上游各 vendor 方法返回型別不一,通常是 str(已格式化的 CSV/表格/JSON)。
    本函式儘量相容常見 method_name。**未識別的 method 返回空串,觸發上游預設 vendor。**

    所有分支都以「標的元資訊」開頭,避免 LLM 在 A 股 ticker 上瞎編公司名。
    """
    method = (method_name or "").lower()
    header = _stock_meta_header(symbol)

    # 1a) 單指標查詢:get_indicators(symbol, indicator_name, curr_date, look_back_days)
    # 上游對每個技術指標(macd/rsi/kdj/boll/...)各調一次,8 次返回相同 K線 CSV 是浪費。
    # 我們按 indicator 名返回簡短的"該指標當前值 + 簡要解讀",避免重複汙染上下文。
    if "indicator" in method:
        if args and len(args) >= 2:
            indicator = str(args[1]).lower()
            return f"{header}\n\n{_render_single_indicator(indicator, symbol)}"
        # 沒傳 indicator 引數:降級到 K 線 CSV
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No data available for indicators on {symbol}]"

    # 1b) K 線 / 股價完整 CSV:get_stockstats / get_yfin_data / get_stock_data
    if any(k in method for k in (
        "stockstats", "yfin", "ohlcv", "kline", "price", "stock_data",
    )):
        klines = _cache().get("klines") or []
        if klines:
            return f"{header}\n\n{_klines_to_csv(klines)}"
        return f"{header}\n\n[No kline data available from PanWatch for {symbol}]"

    # 2) 公告/事件/新聞:get_finnhub_news / get_news / get_events / get_global_news / get_insider_*
    if any(k in method for k in ("news", "event", "announce", "insider")):
        events = _cache().get("events") or []
        if events:
            return f"{header}\n\n{_events_to_text(events, limit=20)}"
        return (
            f"{header}\n\n[No company-specific news/events available for {symbol}. "
            "DO NOT pull unrelated global news as a substitute — focus the analysis "
            "on the metadata above and other tool outputs.]"
        )

    # 3) 資金流(主力資金淨流入)— 注意:不要匹配 "cashflow" / "cash_flow",那是現金流量表
    if "capital" in method or ("flow" in method and "cash" not in method):
        flow = _cache().get("capital_flow")
        if flow:
            return f"{header}\n\n{_flow_to_text(flow)}"
        return f"{header}\n\n[No capital flow data available for {symbol}]"

    # 4) 基本面 / 財報:有真實 akshare 財務資料時返回完整指標,否則 fallback 到 quote
    financial = _cache().get("financial")
    if "fundamental" in method or "financial" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_fundamentals_summary
            return f"{header}\n\n{render_fundamentals_summary(financial)}"
        return f"{header}\n\n{_quote_to_lightweight_fundamentals(symbol)}"
    if "income" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_income_statement
            return f"{header}\n\n{render_income_statement(financial)}"
        return (
            f"{header}\n\n[Income statement not available for {symbol}. "
            "Avoid invented revenue/earnings numbers.]"
        )
    if "balance" in method or "sheet" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_balance_sheet
            return f"{header}\n\n{render_balance_sheet(financial)}"
        return (
            f"{header}\n\n[Balance sheet not available for {symbol}. "
            "Avoid invented assets/liabilities numbers.]"
        )
    if "cashflow" in method or "cash_flow" in method:
        if financial:
            from src.modules.automation.tradingagents.data_context import render_cashflow
            return f"{header}\n\n{render_cashflow(financial)}"
        return (
            f"{header}\n\n[Cash flow statement not available for {symbol}. "
            "Avoid invented cash flow numbers.]"
        )

    # 未識別:讓上游走預設 vendor
    raise NotImplementedError(f"no panwatch backing for {method_name}")


def _serve_keyword_news(keyword: str) -> str:
    """即時按行業/主題關鍵詞搜中文新聞(東方財富搜尋),格式化返回。

    用於 get_news 的 query 是行業/主題詞(非 ticker,如"汽車行業""新能源汽車")時,
    替代拉不到中文資料的上游 vendor。md_news_by_keyword 本身同步,直接呼叫即可。
    """
    from src.platform.marketdata.marketdata_client import md_news_by_keyword

    items = md_news_by_keyword(keyword)
    if not items:
        return (
            f"[未搜到「{keyword}」相關行業/主題新聞。請基於個股新聞 + 元資訊分析,"
            "不要編造行業新聞。]"
        )
    lines = [f"[行業/主題新聞「{keyword}」(來自東方財富,共 {len(items)} 條)]"]
    for it in items[:15]:
        ts = getattr(it, "publish_time", "")
        title = getattr(it, "title", "") or ""
        lines.append(f"- [{ts}] {title}")
    return "\n".join(lines)


def _render_single_indicator(indicator: str, symbol: str) -> str:
    """按 indicator 名(macd/rsi/kdj/boll/...)返回該指標當前值,而不是全 K 線 CSV。

    資料來源:KlineCollector.get_technical_indicators 已經算好的 dataclass。
    """
    tech = _cache().get("technical")
    if not tech:
        # 沒預計算時,fallback 到 K 線 CSV(讓 LLM 自己算)
        klines = _cache().get("klines") or []
        if klines:
            return (
                f"[Indicator query: {indicator}] (no precomputed value, "
                f"returning raw K-line CSV for self-calculation)\n\n"
                f"{_klines_to_csv(klines[-30:])}"  # 僅 30 條夠
            )
        return f"[No data available for indicator '{indicator}' on {symbol}]"

    ind = indicator.lower()
    lines = [f"[Technical Indicator: {indicator.upper()}] for {symbol}"]
    handled = False

    def _g(name):
        return _attr(tech, name, None)

    if "macd" in ind:
        dif, dea, hist = _g("macd_dif"), _g("macd_dea"), _g("macd_hist")
        cross = _g("macd_cross") or ""
        lines.append(f"- DIF: {dif} | DEA: {dea} | Hist: {hist}")
        if cross:
            lines.append(f"- Latest cross: {cross}")
        handled = True
    if "rsi" in ind:
        lines.append(f"- RSI(6): {_g('rsi6')} | RSI(12): {_g('rsi12')} | RSI(24): {_g('rsi24')}")
        st = _g("rsi_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "kdj" in ind:
        lines.append(f"- K: {_g('kdj_k')} | D: {_g('kdj_d')} | J: {_g('kdj_j')}")
        st = _g("kdj_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if "boll" in ind:
        lines.append(
            f"- Upper: {_g('boll_upper')} | Mid: {_g('boll_mid')} | Lower: {_g('boll_lower')}"
        )
        st = _g("boll_status")
        if st:
            lines.append(f"- Status: {st}")
        handled = True
    if any(x in ind for x in ("ma", "sma", "ema")) and not handled:
        lines.append(
            f"- MA5: {_g('ma5')} | MA10: {_g('ma10')} | MA20: {_g('ma20')} | MA60: {_g('ma60')}"
        )
        trend = _g("trend")
        if trend:
            lines.append(f"- Trend: {trend}")
        handled = True
    if any(x in ind for x in ("vol", "volume")):
        lines.append(f"- Volume ratio: {_g('volume_ratio')} | Trend: {_g('volume_trend')}")
        handled = True

    if not handled:
        # 未識別的指標:傾倒全部技術指標摘要
        lines.append("(indicator name not specifically recognized — returning full snapshot)")
        for attr in (
            "ma5", "ma10", "ma20", "ma60",
            "macd_dif", "macd_dea", "macd_hist", "macd_cross",
            "rsi6", "rsi12", "rsi24", "rsi_status",
            "kdj_k", "kdj_d", "kdj_j", "kdj_status",
            "boll_upper", "boll_mid", "boll_lower", "boll_status",
            "volume_ratio", "volume_trend", "trend",
        ):
            v = _g(attr)
            if v is not None and v != "":
                lines.append(f"- {attr}: {v}")
    return "\n".join(lines)


def _quote_to_lightweight_fundamentals(symbol: str) -> str:
    """從 quote 拉"輕量基本面"(市值/PE/周轉率/成交額),給 LLM 一些真實資料。"""
    quote = _cache().get("quote") or {}
    if not isinstance(quote, dict):
        return f"[No lightweight fundamentals available for {symbol}]"

    lines = ["[Lightweight Fundamentals (from PanWatch real-time quote)]"]
    fields = [
        ("PE ratio", "pe_ratio"),
        ("Total market cap", "total_market_value"),
        ("Circulating market cap", "circulating_market_value"),
        ("Turnover rate (%)", "turnover_rate"),
        ("Current price", "current_price"),
        ("Today change (%)", "change_pct"),
        ("Today open", "open_price"),
        ("Today high", "high_price"),
        ("Today low", "low_price"),
        ("Prev close", "prev_close"),
        ("Volume (shares)", "volume"),
        ("Turnover (CNY)", "turnover"),
    ]
    has_any = False
    for label, key in fields:
        v = quote.get(key)
        if v is not None and v != "":
            lines.append(f"- {label}: {v}")
            has_any = True
    if not has_any:
        return f"[No lightweight fundamentals available for {symbol}]"
    lines.append("")
    lines.append(
        "Note: This is real-time market data, NOT a substitute for full financial "
        "statements. Use it as a sanity check (e.g. valuation level via P/E, liquidity "
        "via turnover) rather than as the basis for revenue/earnings claims."
    )
    return "\n".join(lines)


def _klines_to_csv(klines) -> str:
    """KlineData list → CSV 字串。

    TradingAgents 上游期望:date,open,high,low,close,volume
    """
    if not klines:
        return "date,open,high,low,close,volume\n"
    lines = ["date,open,high,low,close,volume"]
    for k in klines:
        date_v = getattr(k, "date", None) or (k.get("date") if isinstance(k, dict) else "")
        open_v = _attr(k, "open")
        high_v = _attr(k, "high")
        low_v = _attr(k, "low")
        close_v = _attr(k, "close")
        vol_v = _attr(k, "volume")
        lines.append(f"{date_v},{open_v},{high_v},{low_v},{close_v},{vol_v}")
    return "\n".join(lines)


def _events_to_text(events, limit: int = 20) -> str:
    if not events:
        return "無近期公告/事件"
    out = []
    for ev in events[:limit]:
        title = getattr(ev, "title", None) or (
            ev.get("title") if isinstance(ev, dict) else str(ev)
        )
        ts = getattr(ev, "publish_time", None) or (
            ev.get("publish_time") if isinstance(ev, dict) else ""
        )
        out.append(f"- [{ts}] {title}")
    return "\n".join(out)


def _flow_to_text(flow) -> str:
    if isinstance(flow, list):
        flow = flow[0] if flow else None
    if not flow:
        return "無資金流向資料"
    main_net = _attr(flow, "main_net_inflow")
    main_pct = _attr(flow, "main_net_inflow_pct")
    return f"主力淨流入:{main_net} / {main_pct}%"


def _attr(obj, name, default=""):
    if hasattr(obj, name):
        v = getattr(obj, name)
        return v if v is not None else default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default
