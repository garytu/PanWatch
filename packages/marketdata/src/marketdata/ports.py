"""解耦埠:宿主實現這兩個 Protocol 即可接入,包本身不依賴 web/DB。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class SourceConfig:
    """一個資料源的執行配置(由 ConfigProvider 提供)。"""

    vendor: str
    priority: int = 100
    enabled: bool = True
    config: dict = field(default_factory=dict)   # 憑證/引數:token / cookies / proxy ...
    supports_batch: bool = False


@runtime_checkable
class ConfigProvider(Protocol):
    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        """返回該 datatype 在該 market 下、按優先順序排序的源列表。"""
        ...


@runtime_checkable
class MetricsSink(Protocol):
    def record(self, *, vendor: str, datatype: str, market: str | None,
               ok: bool, count: int, latency_ms: int, error: str = "") -> None:
        ...
