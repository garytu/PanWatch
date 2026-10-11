"""行情/資料採集的統一 HTTP 工具。

把散落在各 collector 的樣板收斂到一處,避免每個檔案各寫一套且各有缺漏:
- **走系統代理**:預設 trust_env=True,遵循程式 env 的 HTTP_PROXY/NO_PROXY(由 apply_proxy_env 按 UI 的 http_proxy 設定)。沒配代理時即直連。
- **按 host 節流**:同一域名請求最小間隔,平滑順序/併發突發(第三方批次突發會限流)。
- **退避重試**:空回應/異常退避 + 抖動重試。
- **呼叫來源標記**:全專案共享一個 contextvar,失敗日誌帶 [src=xxx],定位是哪個任務觸發。

來源標記是全域性共享的:任何排程入口 `with fetch_source("xxx"):` 包裹後,
該任務內所有 collector(K線/報價/資金流/...)的失敗日誌都會帶上同一來源。
asyncio.to_thread 會傳播 contextvars,非同步排程裡設定也能透到 worker 執行緒。
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import ExitStack, contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── 呼叫來源標記(全域性共享)──────────────────────────────────────────────
_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fetch_source", default=""
)


@contextmanager
def fetch_source(name: str):
    """標註當前取數的呼叫來源,寫入失敗日誌便於定位觸發方。

    同步透傳到 marketdata 包自己的 HTTP contextvar；否則宿主排程器雖然
    已經標記了 ``outcome_eval``，包內騰訊/Stooq 日誌仍會顯示為空來源。
    """
    token = _FETCH_SOURCE.set(name or "")
    stack = ExitStack()
    try:
        try:
            from marketdata.http import fetch_source as package_fetch_source

            stack.enter_context(package_fetch_source(name))
        except Exception:
            # marketdata 是可選依賴；宿主採集器本身仍應能獨立工作。
            pass
        yield
    finally:
        stack.close()
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# ── 按 host 程式級節流 ───────────────────────────────────────────────────
_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """保證對同一 host 的請求間隔 ≥ min_interval_s,平滑順序/併發突發。"""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


# ── 統一同步 GET ─────────────────────────────────────────────────────────
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
    parse: str = "text",  # "text" | "json" | "content"
    encoding: str | None = None,  # 強制解碼(如 "gbk")
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,  # 遵循程式 env 代理(HTTP_PROXY/NO_PROXY),由 apply_proxy_env 統一設
    follow_redirects: bool = True,
    verify: bool = True,
) -> Any | None:
    """按系統代理(env)+ host 節流 + 退避重試。成功返回解析結果,失敗返回 None 並打帶來源的日誌。"""
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
    return None


# ── 輕量 TTL 快取 ────────────────────────────────────────────────────────
# 與 src/core/providers/cache.py 等價,但定義在採集層最底層模組,供各 collector
# 直接複用——避免 collector 反向 import providers 包觸發迴圈依賴。
class TTLCache:
    """單程式記憶體 TTL 快取,執行緒安全,過期 key 在下次 get 時被動剔除。"""

    def __init__(self, default_ttl_sec: float = 20.0, max_size: int = 2048):
        self._default_ttl = default_ttl_sec
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if expires_at <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_sec: float | None = None) -> None:
        ttl = ttl_sec if ttl_sec is not None else self._default_ttl
        if ttl <= 0:
            return  # 顯式不快取
        expires = time.monotonic() + ttl
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest = min(self._store.items(), key=lambda kv: kv[1][1])
                del self._store[oldest[0]]
            self._store[key] = (value, expires)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
