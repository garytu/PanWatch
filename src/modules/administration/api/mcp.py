"""MCP Server —— 把 chat 的 5 個只讀工具暴露為 Model Context Protocol 端點。

設計選擇(在報告中說明):
- **手寫輕量 JSON-RPC**(Streamable HTTP 的 JSON 回應模式),不引入 mcp SDK ——
  依賴最小、測試自包含、協議表面小(只讀場景僅需 initialize/tools/list/tools/call);
- 掛在**頂層 `/mcp`**(不在 `/api/` 下),繞開 ResponseWrapperMiddleware 的
  `{code,data,message}` 包裝,保證 JSON-RPC 報文原樣返回;
- 鑑權用**獨立 PAT 體系**(pwmcp_ 字首 + sha256 存庫 + 常數時間比較 + mcp:read
  scope),與登入 JWT 分流;工具全只讀,天然安全;每次呼叫落審計日誌。

工具實現複用 assistant 的公開工具 schema 與 dispatcher，不重寫業務邏輯。
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.modules.administration.pat import (
    SCOPE_MCP_READ,
    hash_token,
    looks_like_pat,
    verify_pat_hash,
)
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS, execute_chat_tool
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import MCPCallLog, PersonalAccessToken

logger = logging.getLogger(__name__)
router = APIRouter()

# 協議版本(使用者端未協商時的預設值)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "PanWatch", "version": "0.1.0"}

# 只讀工具白名單(複用 chat 的工具定義,新增工具自動納入)
READ_TOOL_NAMES = {t["function"]["name"] for t in CHAT_TOOLS}

# last_used 寫入節流視窗(秒),避免每次 tool call 都寫庫
_LAST_USED_THROTTLE_S = 60
# 審計摘要長度上限
_ARG_SUMMARY_MAX = 200
_ARG_VALUE_MAX = 40


# ──────────────── PAT 鑑權 ────────────────


def _to_utc(dt: datetime | None) -> datetime | None:
    """SQLite 存的 DateTime 是 naive,統一按 UTC 處理,避免 aware/naive 比較報錯。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bump_last_used(db: Session, row: PersonalAccessToken, request: Request) -> None:
    now = datetime.now(timezone.utc)
    last = _to_utc(row.last_used_at)
    if last is None or (now - last).total_seconds() > _LAST_USED_THROTTLE_S:
        row.last_used_at = now.replace(tzinfo=None)
        row.last_used_ip = request.client.host if request.client else None
        db.commit()


def authenticate_pat(request: Request, db: Session) -> dict:
    """校驗 Authorization: Bearer pwmcp_...，返回 PAT 後設資料；失敗拋 HTTPException。"""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Bearer PAT")
    token = header[7:].strip()
    if not looks_like_pat(token):
        raise HTTPException(403, "MCP 端點需要 PAT(pwmcp_ 字首)")

    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == hash_token(token))
        .first()
    )
    if row is None or not verify_pat_hash(token, row.token_hash):
        raise HTTPException(401, "無效的 token")
    if row.revoked_at is not None:
        raise HTTPException(401, "token 已吊銷")
    exp = _to_utc(row.expires_at)
    if exp is not None and exp < datetime.now(timezone.utc):
        raise HTTPException(401, "token 已過期")

    try:
        scopes = set(json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = set()
    if SCOPE_MCP_READ not in scopes:
        raise HTTPException(403, f"PAT 缺少所需 scope: {SCOPE_MCP_READ}")

    _bump_last_used(db, row, request)
    return {
        "id": row.id,
        "prefix": row.prefix,
        "name": row.name,
        "client_ip": request.client.host if request.client else None,
    }


# ──────────────── 審計日誌 ────────────────


def _summarize_args(args: dict) -> str | None:
    """脫敏摘要:結構化欄位 k=v，截斷，供審計除錯。"""
    if not args:
        return None
    parts: list[str] = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str):
            shown = v if len(v) <= _ARG_VALUE_MAX else v[: _ARG_VALUE_MAX - 1] + "…"
        elif isinstance(v, (list, tuple)):
            shown = f"[{len(v)}]"
        elif isinstance(v, dict):
            shown = f"{{{len(v)}}}"
        else:
            shown = repr(v)
        parts.append(f"{k}={shown}")
    summary = ", ".join(parts)
    return summary[:_ARG_SUMMARY_MAX] if summary else None


def _write_call_log(
    pat: dict,
    tool_name: str,
    status: str,
    error: str | None,
    args_summary: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """落審計日誌(獨立 session，失敗靜默不影響主流程)。"""
    db = SessionLocal()
    try:
        db.add(
            MCPCallLog(
                pat_id=pat.get("id"),
                pat_prefix=pat.get("prefix"),
                tool_name=(tool_name or "")[:200],
                status=status,
                error_message=(str(error)[:500] if error else None),
                args_summary=args_summary,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
        )
        db.commit()
    except Exception:
        logger.warning("寫入 MCPCallLog 失敗", exc_info=True)
        db.rollback()
    finally:
        db.close()


MCP_LOG_RETENTION_DAYS = 30


def prune_mcp_logs(retention_days: int = MCP_LOG_RETENTION_DAYS) -> int:
    """清理超過保留期的 MCP 呼叫日誌,返回刪除條數(供每日排程呼叫)。"""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(MCPCallLog).filter(MCPCallLog.called_at < cutoff).delete()
        )
        db.commit()
        if deleted:
            logger.info("MCP 呼叫日誌保留期清理: 刪除 %d 條", deleted)
        return deleted
    except Exception:
        logger.warning("MCP 呼叫日誌清理失敗", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


# ──────────────── JSON-RPC 處理 ────────────────


def _mcp_tools() -> list[dict]:
    """CHAT_TOOLS(OpenAI function schema)→ MCP tool 列表。"""
    tools = []
    for t in CHAT_TOOLS:
        fn = t["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _rpc_result(req_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def _handle_tools_call(params: dict, db: Session, pat: dict, req_id) -> JSONResponse:
    import time

    name = params.get("name")
    args = params.get("arguments") or {}
    if not name:
        return _rpc_error(req_id, -32602, "缺少工具名 name")
    if name not in READ_TOOL_NAMES:
        _write_call_log(pat, str(name), "error", "unknown tool", None, 0, pat.get("client_ip"))
        return _rpc_error(req_id, -32602, f"未知或不允許的工具: {name}")

    start = time.perf_counter()
    err: str | None = None
    try:
        text = await execute_chat_tool(db, name, args if isinstance(args, dict) else {})
        is_error = text.startswith("工具執行出錯")
        if is_error:
            err = text
        return _rpc_result(
            req_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    except Exception as e:  # noqa: BLE001 — 兜底,不讓異常穿透協議層
        err = str(e)
        return _rpc_error(req_id, -32603, f"工具執行異常: {e}")
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _write_call_log(
            pat,
            str(name),
            "error" if err else "ok",
            err,
            _summarize_args(args if isinstance(args, dict) else {}),
            duration_ms,
            pat.get("client_ip"),
        )


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    """MCP Streamable HTTP 單端點:處理 initialize / tools/list / tools/call 等。"""
    pat = authenticate_pat(request, db)

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "JSON 解析失敗")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "僅支援單條 JSON-RPC 請求")

    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    # 通知類訊息(無 id)不需要回應,返回 202
    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return _rpc_result(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _mcp_tools()})
    if method == "tools/call":
        return await _handle_tools_call(params, db, pat, req_id)

    return _rpc_error(req_id, -32601, f"方法不支援: {method}")
