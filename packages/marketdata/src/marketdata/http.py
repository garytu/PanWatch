"""統一 HTTP 工具:走系統代理(trust_env=True)+ 按 host 節流 + 退避重試 + 來源標記。

預設 trust_env=True —— 遵循程式 env 的 HTTP_PROXY/NO_PROXY(宿主按 UI 的 http_proxy 設定統一注入);
沒配代理時即直連。個別呼叫可用 proxy= 顯式覆蓋。
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("fetch_source", default="")


@contextmanager
def fetch_source(name: str):
    """標註取數來源,寫入失敗日誌便於定位觸發方。"""
    token = _FETCH_SOURCE.set(name or "")
    try:
        yield
    finally:
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# 失敗原因收集:預設 None = 不收集(生產熱路徑零開銷)。資料來源"測試"按鈕用 capture_errors()
# 包住取數呼叫,把 market_get / vendor 的真實失敗原因收上來透到 UI,而不是隻顯示"無資料"。
_ERROR_SINK: contextvars.ContextVar[list | None] = contextvars.ContextVar("md_error_sink", default=None)


@contextmanager
def capture_errors():
    """進入後,market_get / record_error 的失敗原因會被收集到 yield 出的 list。"""
    errs: list[str] = []
    token = _ERROR_SINK.set(errs)
    try:
        yield errs
    finally:
        _ERROR_SINK.reset(token)


def record_error(msg: str) -> None:
    """把一條失敗原因寫入當前 capture_errors 上下文(無上下文則忽略)。
    供 vendor 自己 catch 異常(如 yfinance 走庫、不經 market_get)時也能上報真因。"""
    sink = _ERROR_SINK.get()
    if sink is not None and msg:
        sink.append(msg)


_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """保證對同一 host 的請求間隔 ≥ min_interval_s。"""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


def market_get(
    url: str,
    *,
    host_key: str,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "text",   # "text" | "json" | "content"
    encoding: str | None = None,
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,
    follow_redirects: bool = True,
    verify: bool = True,
    proxy: str | None = None,
) -> Any | None:
    """走系統代理(env)+ 按 host 節流 + 退避重試。成功返回解析結果,失敗返回 None 並打帶來源日誌。

    proxy: 顯式代理,僅在給了值時傳給 httpx.Client 覆蓋 env 代理;不傳則遵循 trust_env(env)。
    """
    effective_proxy = proxy
    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
                **({"proxy": effective_proxy} if effective_proxy else {}),
            ) as client:
                resp = client.get(url, params=params)
                if raise_for_status:
                    resp.raise_for_status()
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                if encoding:
                    return resp.content.decode(encoding, errors="ignore")
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        label = log_label or host_key
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} 獲取失敗{sym}: {last_err}{source_suffix()}")
        record_error(f"{label}{sym}: {type(last_err).__name__}: {last_err}")
    return None
