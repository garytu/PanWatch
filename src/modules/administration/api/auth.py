"""認證 API - 簡單的單使用者 JWT 認證"""
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt

from src.platform.persistence.database import get_db, SessionLocal
from src.platform.persistence.models import AppSettings

router = APIRouter()
security = HTTPBearer(auto_error=False)

# JWT 配置
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# 環境變數配置（Docker 部署用）
ENV_AUTH_USERNAME = os.getenv("AUTH_USERNAME")
ENV_AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

# 設定項 key
AUTH_USERNAME_KEY = "auth_username"
PASSWORD_HASH_KEY = "auth_password_hash"
JWT_SECRET_KEY = "jwt_secret"

# JWT Secret 快取
_jwt_secret: str | None = None


def get_jwt_secret() -> str:
    """獲取 JWT Secret（持久化到資料庫）"""
    global _jwt_secret
    if _jwt_secret:
        return _jwt_secret

    # 環境變數優先
    if os.getenv("JWT_SECRET"):
        _jwt_secret = os.getenv("JWT_SECRET")
        return _jwt_secret

    # 從資料庫讀取或首次生成
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == JWT_SECRET_KEY).first()
        if setting:
            _jwt_secret = setting.value
        else:
            _jwt_secret = secrets.token_hex(32)
            db.add(AppSettings(key=JWT_SECRET_KEY, value=_jwt_secret, description="JWT簽名金鑰(自動生成)"))
            db.commit()
        return _jwt_secret
    finally:
        db.close()


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str


def hash_password(password: str) -> str:
    """簡單的密碼雜湊"""
    return hashlib.sha256(password.encode()).hexdigest()


def create_token(expires_days: int = JWT_EXPIRE_DAYS) -> tuple[str, datetime]:
    """建立 JWT token"""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=expires_days)
    payload = {
        "exp": expires_at,
        "iat": now,
        "sub": "user",
    }
    token = jwt.encode(payload, get_jwt_secret(), algorithm=JWT_ALGORITHM)
    return token, expires_at


def verify_token(token: str) -> bool:
    """驗證 JWT token"""
    try:
        jwt.decode(token, get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def get_stored_username(db: Session) -> Optional[str]:
    """獲取儲存的使用者名稱"""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    return setting.value if setting else None


def set_stored_username(db: Session, username: str):
    """設定使用者名稱"""
    setting = db.query(AppSettings).filter(AppSettings.key == AUTH_USERNAME_KEY).first()
    if setting:
        setting.value = username
    else:
        setting = AppSettings(key=AUTH_USERNAME_KEY, value=username, description="認證使用者名稱")
        db.add(setting)
    db.commit()


def get_password_hash(db: Session) -> Optional[str]:
    """獲取儲存的密碼雜湊"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    return setting.value if setting else None


def set_password_hash(db: Session, password_hash: str):
    """設定密碼雜湊"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    if setting:
        setting.value = password_hash
    else:
        setting = AppSettings(key=PASSWORD_HASH_KEY, value=password_hash, description="認證密碼雜湊")
        db.add(setting)
    db.commit()


def init_auth_from_env(db: Session) -> bool:
    """從環境變數初始化認證（Docker 部署用）

    Returns:
        True if initialized from env, False otherwise
    """
    if not ENV_AUTH_USERNAME or not ENV_AUTH_PASSWORD:
        return False

    # 如果已有帳號，不覆蓋
    if get_password_hash(db):
        return False

    # 從環境變數建立帳號
    set_stored_username(db, ENV_AUTH_USERNAME)
    set_password_hash(db, hash_password(ENV_AUTH_PASSWORD))
    return True


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """驗證當前使用者（用作依賴）"""
    # 檢查是否已設定密碼
    password_hash = get_password_hash(db)
    if not password_hash:
        # 未設定密碼，允許訪問（初始狀態）
        return None

    # 已設定密碼，需要驗證 token
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登入",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入已過期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "user"


@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """獲取認證狀態"""
    password_hash = get_password_hash(db)
    return {
        "initialized": password_hash is not None,
    }


@router.post("/setup", response_model=TokenResponse)
async def setup_password(data: SetupRequest, db: Session = Depends(get_db)):
    """首次設定使用者名稱和密碼"""
    if get_password_hash(db):
        raise HTTPException(400, "已設定過帳號，請使用登入介面")

    if not data.username or len(data.username) < 2:
        raise HTTPException(400, "使用者名稱長度至少 2 位")

    if len(data.password) < 6:
        raise HTTPException(400, "密碼長度至少 6 位")

    set_stored_username(db, data.username)
    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登入"""
    stored_hash = get_password_hash(db)
    stored_username = get_stored_username(db)
    if not stored_hash or not stored_username:
        raise HTTPException(400, "請先設定帳號")

    if data.username != stored_username:
        raise HTTPException(401, "使用者名稱或密碼錯誤")

    if hash_password(data.password) != stored_hash:
        raise HTTPException(401, "使用者名稱或密碼錯誤")

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/change-password")
async def change_password(
    data: SetupRequest,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
):
    """修改密碼"""
    if len(data.password) < 6:
        raise HTTPException(400, "密碼長度至少 6 位")

    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    return {"message": "密碼已更新"}


@router.get("/me")
async def get_me(user: str = Depends(get_current_user)):
    """獲取當前使用者資訊"""
    return {"user": user or "guest"}
