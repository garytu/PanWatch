"""執行中排程器的輕量登入檔,供系統自檢讀取健康狀態。

各排程器 start() 時把自身的 APScheduler(有 .running / .get_jobs())register 進來;
自檢的 probe_scheduler 據此判斷"排程器是否在跑"。CLI 等無排程的程式登入檔為空 → 優雅跳過。
"""

from __future__ import annotations

_REGISTRY: dict[str, object] = {}


def register(name: str, scheduler: object) -> None:
    _REGISTRY[name] = scheduler


def get_all() -> dict[str, object]:
    return dict(_REGISTRY)


def clear() -> None:
    _REGISTRY.clear()
