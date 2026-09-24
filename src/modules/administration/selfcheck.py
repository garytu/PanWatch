"""系統自檢(Doctor):一鍵體檢 資料來源 / AI / 通知,帶中文修復提示。

複用各自現有的 test 邏輯(資料來源 manager.test_source、AI AIClient.chat、通知 NotifierManager),
不重造探測;補兩件事:① 併發聚合成一塊看板 ② 常見錯誤 → 中文 actionable 修復提示。

通知預設**只校驗 URI 配置不真發**(防刷屏);notify_send=True 才真實傳送。
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.platform.persistence.database import SessionLocal

logger = logging.getLogger(__name__)

SLOW_MS = 4000          # 超過算「慢」
PROBE_TIMEOUT_S = 20    # 單項探測超時


def classify_hint(category: str, error: str | None) -> str:
    """錯誤 → 中文 actionable 修復提示。覆蓋自託管最常見的代理/鑑權/配置坑。"""
    e = (error or "").lower()
    if category == "datasource":
        if "database is locked" in e:
            return "SQLite 被鎖:併發排程疊加慢代理所致,降低併發或加快/關閉代理。"
        if any(k in e for k in (
            "server disconnected", "timeout", "timed out", "connect", "proxy",
            "ssl", "remote end closed", "read timed out", "connection reset",
        )):
            return "行情/新聞介面連線失敗:所有請求走 http_proxy 設定的系統代理,檢查該代理能否代出目標域名(國內介面需 CN 出口、Yahoo 需境外),或本機被 MITM 代理攔截需換可信出口。"
        return "資料來源不通:開啟資料來源配置頁看詳細日誌,確認 provider 與介面可達。"
    if category == "ai":
        if any(k in e for k in ("401", "unauthorized", "invalid_api_key", "api key", "incorrect api key", "authentication")):
            return "AI 鑑權失敗:API Key 不對或失效,檢查服務商 api_key。"
        if any(k in e for k in ("model", "not found", "does not exist", "404")):
            return "模型不存在:檢查模型名(model)是否與服務商一致。"
        if any(k in e for k in ("429", "rate limit", "quota", "insufficient", "balance")):
            return "被限流或額度不足:稍後重試,或檢查帳戶餘額/額度。"
        if any(k in e for k in ("connect", "timeout", "timed out", "proxy", "ssl", "getaddrinfo", "name resolution")):
            return "連不上 AI 服務:檢查 base_url 是否正確、是否需要/誤用了代理。"
        return "AI 呼叫失敗:逐項檢查 base_url / api_key / model 配置。"
    if category == "notify":
        if any(k in e for k in ("invalid", "unsupported", "scheme", "malformed", "parse", "config")):
            return "通知配置無效:檢查管道 URL/引數格式(Apprise URI)。"
        if any(k in e for k in ("forbidden", "unauthorized", "403", "401", "404", "blocked", "connect", "timeout")):
            return "通知傳送失敗:檢查 webhook 地址/token 是否正確、是否被網路攔截。"
        return "通知不通:核對管道配置,或到管道頁點「測試」做真實傳送驗證。"
    if category == "system":
        if "lock" in e:
            return "SQLite 被鎖:併發排程疊加慢代理所致,降低併發或加快/關閉代理。"
        if any(k in e for k in ("disk", "space", "磁碟", "空間")):
            return "磁碟空間不足:清理 data 目錄舊資料/日誌,或擴容磁碟。"
        if any(k in e for k in ("scheduler", "排程", "stopped", "not running")):
            return "排程器未執行/已停止:重啟服務以恢復定時任務。"
        return error or "系統項異常,檢視日誌。"
    return error or "未知錯誤,檢視日誌。"


def _item(category: str, key: str, name: str, status: str,
          latency_ms: int, error: str | None = None, note: str | None = None) -> dict:
    return {
        "category": category,
        "key": key,
        "name": name,
        "status": status,  # ok | slow | fail
        "latency_ms": int(latency_ms),
        "error": error,
        "hint": classify_hint(category, error) if status == "fail" else "",
        "note": note,
    }


def _status_for(success: bool, latency_ms: int) -> str:
    if not success:
        return "fail"
    return "slow" if latency_ms > SLOW_MS else "ok"


async def probe_datasource(source) -> dict:
    """複用 collector manager.test_source。"""
    from src.modules.market.data_collector import get_collector_manager

    t0 = time.monotonic()
    try:
        result = await get_collector_manager().test_source(source)
        latency = int(getattr(result, "duration_ms", None) or (time.monotonic() - t0) * 1000)
        return _item("datasource", f"ds:{source.id}", source.name,
                     _status_for(bool(result.success), latency), latency,
                     None if result.success else (result.error or "測試未透過"))
    except Exception as e:
        return _item("datasource", f"ds:{source.id}", source.name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_ai_model(model, service) -> dict:
    """複用 AIClient.chat 發一個極短 ping。"""
    from src.platform.ai.ai_client import AIClient

    name = model.name or model.model
    t0 = time.monotonic()
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key, model=model.model)
        await client.chat(system_prompt="You are a helpful assistant.",
                          user_content="Say 'OK'.", temperature=0)
        latency = int((time.monotonic() - t0) * 1000)
        return _item("ai", f"ai:{model.id}", name, _status_for(True, latency), latency)
    except Exception as e:
        return _item("ai", f"ai:{model.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_notify_channel(channel, *, send: bool = False) -> dict:
    """預設只校驗 URI 配置(add_channel 不通會拋);send=True 才真實傳送。"""
    from src.platform.notifications.notifier import NotifierManager

    name = channel.name or channel.type
    t0 = time.monotonic()
    try:
        notifier = NotifierManager()
        notifier.add_channel(channel.type, channel.config or {})  # URI 非法會拋
        if not send:
            latency = int((time.monotonic() - t0) * 1000)
            return _item("notify", f"nc:{channel.id}", name, "ok", latency,
                         note="僅校驗配置格式,未真實傳送(勾選「含真實傳送」可發測試訊息)")
        result = await notifier.notify_with_result(
            title="系統自檢", content="盯盤俠系統自檢測試訊息。", bypass_quiet_hours=True)
        latency = int((time.monotonic() - t0) * 1000)
        ok = bool(result.get("success"))
        return _item("notify", f"nc:{channel.id}", name, _status_for(ok, latency), latency,
                     None if ok else (result.get("error") or "傳送失敗"))
    except Exception as e:
        return _item("notify", f"nc:{channel.id}", name, "fail",
                     int((time.monotonic() - t0) * 1000), str(e))


async def probe_db() -> dict:
    """對真實庫執行 SELECT 1。"""
    from sqlalchemy import text

    from src.platform.persistence.database import SessionLocal

    t0 = time.monotonic()
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        latency = int((time.monotonic() - t0) * 1000)
        return _item("system", "sys:db", "資料庫", _status_for(True, latency), latency)
    except Exception as e:
        return _item("system", "sys:db", "資料庫", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_disk() -> dict:
    """檢查 data 目錄所在盤的可用空間。"""
    import os
    import shutil

    from src.platform.persistence.database import DB_PATH

    t0 = time.monotonic()
    try:
        data_dir = os.path.dirname(os.path.abspath(DB_PATH))
        usage = shutil.disk_usage(data_dir)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        note = f"可用 {free_gb:.1f}GB / 共 {total_gb:.1f}GB"
        latency = int((time.monotonic() - t0) * 1000)
        if free_gb < 0.2:
            return _item("system", "sys:disk", "磁碟空間", "fail", latency,
                         error=f"磁碟空間嚴重不足({note})", note=note)
        status = "slow" if free_gb < 1.0 else "ok"
        return _item("system", "sys:disk", "磁碟空間", status, latency, note=note)
    except Exception as e:
        return _item("system", "sys:disk", "磁碟空間", "fail", int((time.monotonic() - t0) * 1000), str(e))


async def probe_scheduler() -> dict:
    """經 scheduler_registry 看執行中的排程器;登入檔空(CLI/未啟動)→ 優雅跳過。"""
    from src.platform.scheduling import scheduler_registry

    regs = scheduler_registry.get_all()
    if not regs:
        return _item("system", "sys:scheduler", "排程器", "ok", 0,
                     note="當前程式無執行中的排程器(CLI 自檢會跳過此項)")
    running: list[str] = []
    stopped: list[str] = []
    jobs = 0
    for name, sched in regs.items():
        try:
            if getattr(sched, "running", False):
                running.append(name)
                jobs += len(sched.get_jobs())
            else:
                stopped.append(name)
        except Exception:
            stopped.append(name)
    if running:
        note = f"{len(running)} 個排程器執行中,共 {jobs} 個任務"
        if stopped:
            note += f";已停止: {', '.join(stopped)}"
        return _item("system", "sys:scheduler", "排程器", "ok", 0, note=note)
    return _item("system", "sys:scheduler", "排程器", "fail", 0,
                 error=f"排程器已停止: {', '.join(stopped)}")


async def _guard(coro, fallback: dict) -> dict:
    """給每個 probe 套超時;探測自身已 try/except,這裡只兜超時/異常。"""
    try:
        return await asyncio.wait_for(coro, timeout=PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", PROBE_TIMEOUT_S * 1000, f"探測超時(>{PROBE_TIMEOUT_S}s)")
    except Exception as e:  # pragma: no cover - 防禦
        return _item(fallback["category"], fallback["key"], fallback["name"],
                     "fail", 0, str(e))


def _enumerate(db, include_system: bool = True) -> list[dict]:
    """列舉所有待檢項(身份 + ORM 引用),不探測。include_system 加 DB/磁碟/排程 系統基礎項。"""
    from src.platform.persistence.models import AIModel, AIService, DataSource, NotifyChannel

    targets: list[dict] = []
    if include_system:
        targets.append({"category": "system", "key": "sys:db", "name": "資料庫", "group": None, "_kind": "db"})
        targets.append({"category": "system", "key": "sys:disk", "name": "磁碟空間", "group": None, "_kind": "disk"})
        targets.append({"category": "system", "key": "sys:scheduler", "name": "排程器", "group": None, "_kind": "sched"})
    for src in db.query(DataSource).filter(DataSource.enabled.is_(True)).all():
        targets.append({"category": "datasource", "key": f"ds:{src.id}", "name": src.name,
                        "group": None, "_kind": "ds", "_obj": src})
    for model in db.query(AIModel).all():
        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if not service:
            continue
        # group = 服務商名,供前端做「服務商 → 模型」兩級層級
        targets.append({"category": "ai", "key": f"ai:{model.id}", "name": model.name or model.model,
                        "group": service.name, "_kind": "ai", "_obj": model, "_service": service})
    for ch in db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all():
        targets.append({"category": "notify", "key": f"nc:{ch.id}", "name": ch.name or ch.type,
                        "group": None, "_kind": "nc", "_obj": ch})
    return targets


def _identity(t: dict) -> dict:
    return {"category": t["category"], "key": t["key"], "name": t["name"], "group": t.get("group")}


def _probe_for(t: dict, notify_send: bool):
    kind = t["_kind"]
    if kind == "db":
        return probe_db()
    if kind == "disk":
        return probe_disk()
    if kind == "sched":
        return probe_scheduler()
    if kind == "ds":
        return probe_datasource(t["_obj"])
    if kind == "ai":
        return probe_ai_model(t["_obj"], t["_service"])
    return probe_notify_channel(t["_obj"], send=notify_send)


def list_selfcheck_items(*, db=None, include_system: bool = True) -> list[dict]:
    """只列舉待檢項身份(category/key/name/group),不探測;供前端先渲染列表再逐項檢查。"""
    own = db is None
    db = db or SessionLocal()
    try:
        return [_identity(t) for t in _enumerate(db, include_system)]
    finally:
        if own:
            db.close()


async def run_selfcheck(*, db=None, notify_send: bool = False, keys=None, include_system: bool = True) -> dict:
    """探測待檢項,返回看板。keys 非空時只探測這些 key(供前端逐項更新進度)。"""
    own = db is None
    db = db or SessionLocal()
    try:
        keyset = set(keys) if keys is not None else None
        targets = [t for t in _enumerate(db, include_system) if keyset is None or t["key"] in keyset]
        tasks = [_guard(_probe_for(t, notify_send), _identity(t)) for t in targets]
        items = list(await asyncio.gather(*tasks)) if tasks else []
        summary = {
            "total": len(items),
            "ok": sum(1 for i in items if i["status"] == "ok"),
            "slow": sum(1 for i in items if i["status"] == "slow"),
            "fail": sum(1 for i in items if i["status"] == "fail"),
        }
        return {"items": items, "summary": summary, "notify_send": bool(notify_send)}
    finally:
        if own:
            db.close()
