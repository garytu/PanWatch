import base64
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.platform.persistence.database import get_db
from src.platform.persistence.models import AppSettings
from src.platform.runtime.config import Settings
from src.modules.administration.update_checker import check_update

router = APIRouter()

# 模組 router 已不在倉庫根的淺層目錄；版本檔案必須從本檔案的絕對位置推導，
# 不能依賴服務程式的當前工作目錄。
VERSION_FILE = Path(__file__).resolve().parents[4] / "VERSION"


def get_app_version() -> str:
    """獲取應用版本號"""
    # 優先從環境變數讀取
    version = os.getenv("APP_VERSION")
    if version:
        return version

    # 從 VERSION 檔案讀取（支援多個位置）
    possible_paths = [Path("VERSION"), VERSION_FILE]
    for path in possible_paths:
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
    return "dev"


class SettingUpdate(BaseModel):
    value: str


class SettingResponse(BaseModel):
    key: str
    value: str
    description: str

    class Config:
        from_attributes = True


# 配置項描述
SETTING_DESCRIPTIONS = {
    "http_proxy": "HTTP 代理地址(配置後所有對外請求含行情/新聞/AI/通知統一走此代理)",
    "notify_quiet_hours": "通知靜默時間段（HH:MM-HH:MM，空為關閉）",
    "notify_retry_attempts": "通知失敗重試次數（不含首次）",
    "notify_retry_backoff_seconds": "通知重試退避秒數（基數）",
    "notify_dedupe_ttl_overrides": "通知冪等視窗覆蓋（JSON，空為預設）",
    "stock_link_platform": "股票連結平臺（點選股票程式碼跳轉的行情網站）",
    "panwatch_base_url": "PanWatch 公開訪問地址（用於通知裡的分析詳細資訊頁連結，如 https://panwatch.example.com）",
}

SETTING_KEYS = list(SETTING_DESCRIPTIONS.keys())


def _get_env_defaults() -> dict[str, str]:
    """從 .env / 環境變數讀取當前值作為預設"""
    s = Settings()
    return {
        "http_proxy": s.http_proxy,
        "notify_quiet_hours": s.notify_quiet_hours,
        "notify_retry_attempts": str(s.notify_retry_attempts),
        "notify_retry_backoff_seconds": str(s.notify_retry_backoff_seconds),
        "notify_dedupe_ttl_overrides": s.notify_dedupe_ttl_overrides,
        "stock_link_platform": "xueqiu",
        "panwatch_base_url": os.getenv("PANWATCH_BASE_URL", ""),
    }


@router.get("", response_model=list[SettingResponse])
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(AppSettings).all()
    existing_map = {s.key: s for s in settings}

    env_defaults = _get_env_defaults()

    result = []
    for key in SETTING_KEYS:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        env_val = env_defaults.get(key, "")

        if key not in existing_map:
            s = AppSettings(key=key, value=env_val, description=desc)
            db.add(s)
            result.append(s)
        else:
            s = existing_map[key]
            if not s.description:
                s.description = desc
            result.append(s)
    db.commit()

    return result


AVATAR_KEY = "ui_avatar"  # DB 僅存檔名;圖片本體落在 data/avatars/


def _avatar_dir() -> str:
    d = os.path.join(os.environ.get("DATA_DIR", "./data"), "avatars")
    os.makedirs(d, exist_ok=True)
    return d


@router.get("/avatar")
def get_avatar(db: Session = Depends(get_db)):
    """讀取使用者頭像:DB 存檔名,圖片本體在 data/avatars/,讀取後以 data URL 返回。

    GET /avatar 無同名 GET /{key},不存在路由搶匹配問題。
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    fname = (row.value if row and row.value else "").strip()
    if not fname:
        return {"value": ""}
    path = os.path.join(_avatar_dir(), fname)
    if not os.path.isfile(path):
        return {"value": ""}
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {"value": ""}
    mime = "image/png" if fname.lower().endswith(".png") else "image/jpeg"
    return {"value": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"}


@router.put("/avatar")
def set_avatar(update: SettingUpdate, db: Session = Depends(get_db)):
    """儲存/清空使用者頭像:把 data URL 落成 data/avatars/avatar.* 檔案,DB 僅記檔名。

    需在 /{key} 之前註冊以優先匹配。傳空字串即清空(刪檔案 + 清記錄)。
    """
    row = db.query(AppSettings).filter(AppSettings.key == AVATAR_KEY).first()
    old = (row.value if row else "") or ""
    value = (update.value or "").strip()

    if not value:
        if old:
            try:
                os.remove(os.path.join(_avatar_dir(), old))
            except OSError:
                pass
        if row:
            row.value = ""
        db.commit()
        return {"value": ""}

    if not (value.startswith("data:") and "," in value):
        raise HTTPException(400, "頭像需為 data URL")
    header, b64 = value.split(",", 1)
    ext = "png" if "image/png" in header else "jpg"
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(400, "頭像資料無效")

    fname = f"avatar.{ext}"
    with open(os.path.join(_avatar_dir(), fname), "wb") as f:
        f.write(raw)
    if old and old != fname:  # 副檔名變化時清掉舊檔案
        try:
            os.remove(os.path.join(_avatar_dir(), old))
        except OSError:
            pass
    if not row:
        row = AppSettings(key=AVATAR_KEY, value=fname, description="使用者頭像檔名")
        db.add(row)
    else:
        row.value = fname
    db.commit()
    return {"value": fname}


@router.put("/{key}", response_model=SettingResponse)
def update_setting(key: str, update: SettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(AppSettings).filter(AppSettings.key == key).first()
    if not setting:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        setting = AppSettings(key=key, value=update.value, description=desc)
        db.add(setting)
    else:
        setting.value = update.value

    db.commit()
    db.refresh(setting)

    # http_proxy 改動立刻反映到程式 env,所有 httpx(trust_env=True)免重啟即走新代理
    if key == "http_proxy":
        try:
            from server import apply_proxy_env
            apply_proxy_env(update.value)
        except Exception:
            pass

    return setting


@router.get("/version")
def get_version():
    """獲取應用版本號"""
    return {"version": get_app_version()}


@router.get("/update-check")
def get_update_check(db: Session = Depends(get_db)):
    """檢查是否有可用新版本（帶伺服器端快取）。"""
    current = get_app_version()
    app_proxy = (
        db.query(AppSettings)
        .filter(AppSettings.key == "http_proxy")
        .first()
    )
    proxy = (app_proxy.value if app_proxy and app_proxy.value else "").strip() or (
        Settings().http_proxy or ""
    )
    result = check_update(current, proxy=proxy)
    err = str(result.get("error") or "").strip()
    if err:
        return {
            "success": False,
            "code": 10061,
            "message": err,
        }
    return result
