"""個人訪問令牌(PAT)工具。

MCP 端點專用的獨立鑑權體系,與登入 JWT 分流(參考 BeeCount-Cloud 模式):

- 明文字首 ``pwmcp_``(便於使用者與 secret scanner 識別,類比 GitHub ``ghp_``);
- 建立時僅返回一次明文,庫裡只存 sha256(``token_hash``);
- 校驗用 ``hmac.compare_digest`` 常數時間比較,防 timing attack;
- 不用 bcrypt/PBKDF2 —— token 本身已是 256bit 隨機熵,不像密碼需抗暴破,
  且 MCP 每次 tool call 都要校驗一次,sha256 + 常數時間比較又快又夠安全。

分流保證(PAT 只能進 MCP 端點):
- 普通 API 走 JWT(auth.get_current_user),PAT(pwmcp_ 字首)不是合法 JWT → 被拒;
- MCP 端點走 PAT 校驗,非 pwmcp_ 字首的 JWT → 被拒。
"""

import hashlib
import hmac
import secrets

PAT_PREFIX = "pwmcp_"
PAT_RANDOM_BYTES = 32          # token_urlsafe 後約 43 字元,256bit 熵
PAT_DISPLAY_PREFIX_LEN = 14    # 明文前 14 字元,如 pwmcp_a1b2c3d4

# MCP scope(全只讀)
SCOPE_MCP_READ = "mcp:read"


def hash_token(token: str) -> str:
    """sha256 十六進位制摘要。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """生成一個 PAT。

    Returns:
        (plaintext, token_hash, display_prefix) —— plaintext 只在建立時返回一次。
    """
    raw = secrets.token_urlsafe(PAT_RANDOM_BYTES)
    plaintext = f"{PAT_PREFIX}{raw}"
    return plaintext, hash_token(plaintext), plaintext[:PAT_DISPLAY_PREFIX_LEN]


def looks_like_pat(token: str) -> bool:
    """按字首判斷是否 PAT(用於鑑權路由分流,避免每個請求兩遍解碼)。"""
    return bool(token) and token.startswith(PAT_PREFIX)


def verify_pat_hash(provided_token: str, stored_hash: str) -> bool:
    """常數時間比較 PAT 的 sha256,防 timing attack。"""
    return hmac.compare_digest(hash_token(provided_token), stored_hash)
