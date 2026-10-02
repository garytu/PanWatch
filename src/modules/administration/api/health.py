"""系統自檢 API。"""

from fastapi import APIRouter, Query

from src.modules.administration.selfcheck import list_selfcheck_items, run_selfcheck

router = APIRouter()


@router.get("/selfcheck")
async def selfcheck(
    notify_send: bool = Query(False, description="是否真實傳送通知測試(預設只校驗配置不傳送)"),
    list_only: bool = Query(False, alias="list", description="只列出待檢項不探測(前端先渲染列表)"),
    keys: str | None = Query(None, description="逗號分隔,只探測這些 key(前端逐項更新進度)"),
):
    """一鍵體檢 資料來源 / AI / 通知。

    - `?list=1`:只返回待檢項身份 `{items:[{category,key,name}]}`,不探測。
    - `?keys=ds:1,ai:2`:只探測這些項(逐項進度)。
    - 無參:探測全部。
    """
    if list_only:
        return {"items": list_selfcheck_items()}
    key_list = [k for k in keys.split(",") if k] if keys else None
    return await run_selfcheck(notify_send=notify_send, keys=key_list)
