import logging
import os
import re

import apprise
import asyncio
import httpx

logger = logging.getLogger(__name__)


def get_global_proxy() -> str:
    """獲取全域性 HTTP 代理設定"""
    try:
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import AppSettings

        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            return setting.value if setting and setting.value else ""
        finally:
            db.close()
    except Exception:
        return ""


def sanitize_for_telegram(content: str) -> str:
    """清理內容以適配 Telegram（移除 HTML 和 Markdown 格式）"""
    # 移除 HTML 標籤
    content = re.sub(r"</?table[^>]*>", "", content)
    content = re.sub(r"</?thead[^>]*>", "", content)
    content = re.sub(r"</?tbody[^>]*>", "", content)
    content = re.sub(r"</?tr[^>]*>", "\n", content)
    content = re.sub(r"</?th[^>]*>", " | ", content)
    content = re.sub(r"</?td[^>]*>", " | ", content)
    content = re.sub(r"</?div[^>]*>", "", content)
    content = re.sub(r"</?span[^>]*>", "", content)
    content = re.sub(r"</?p[^>]*>", "\n", content)
    content = re.sub(r"<br\s*/?>", "\n", content)

    # 移除 Markdown 格式
    # markdown 連結 [label](url) → "label url":Telegram 內聯連結對 localhost/IP:埠 等
    # 非公網地址不渲染(標籤退化成純文本點不了),裸 URL 則會被自動識別為可點選,更穩。
    content = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 \2", content)
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # 移除標題 #
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # 移除粗體 **
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # 移除斜體 *
    content = re.sub(r"__(.+?)__", r"\1", content)  # 移除粗體 __
    content = re.sub(r"_(.+?)_", r"\1", content)  # 移除斜體 _
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # 移除刪除線
    content = re.sub(r"`(.+?)`", r"\1", content)  # 移除行內程式碼
    content = re.sub(
        r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE
    )  # 列表符號改為 ·
    content = re.sub(
        r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE
    )  # 移除有序列表數字

    # 清理多餘空白
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r" +", " ", content)
    return content.strip()


# 管道型別定義 (label + 表單欄位)
CHANNEL_TYPES = {
    "telegram": {
        "label": "Telegram",
        "fields": ["bot_token", "chat_id", "proxy"],
    },
    "bark": {
        "label": "Bark",
        "fields": ["device_key", "server_url"],
    },
    "dingtalk": {
        "label": "釘釘機器人",
        "fields": [
            "token",
            "secret",
            "phones",
            "keyword",
        ],  # keyword 選填：安全設定為“關鍵字”時自動附加
    },
    "wecom": {
        "label": "企業微信機器人",
        "fields": ["webhook_key"],
    },
    "lark": {
        "label": "飛書機器人",
        "fields": ["webhook_token"],
    },
    "serverchan": {
        "label": "Server醬",
        "fields": ["sendkey"],
    },
    "pushplus": {
        "label": "PushPlus",
        "fields": ["token", "topic"],
    },
    "discord": {
        "label": "Discord",
        "fields": ["webhook_id", "webhook_token"],
    },
    "pushover": {
        "label": "Pushover",
        "fields": ["user_key", "app_token"],
    },
}

# 透過 Apprise 支援的管道型別（無代理配置時）
_APPRISE_TYPES = {"telegram", "bark", "dingtalk", "lark", "discord", "pushover"}

# 自定義實現的管道型別（帶代理或特殊需求）
_CUSTOM_IMPL_TYPES = {"wecom", "serverchan", "pushplus"}

# 支援 Markdown 的管道（不需要 sanitize）
_MARKDOWN_CHANNELS = {"wecom", "serverchan", "pushplus", "dingtalk", "lark", "discord"}

# 不支援 Markdown 的管道（需要 sanitize）
_PLAIN_TEXT_CHANNELS = {"telegram", "bark", "pushover"}


def build_apprise_url(channel_type: str, config: dict) -> str | None:
    """
    根據管道型別和配置構建 Apprise URL

    Returns:
        Apprise URL 或 None（如果需要使用自定義方式傳送，如帶代理的 Telegram）
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")
        # 如果配置了代理（管道級或全域性），返回 None，使用自定義方式傳送
        proxy = config.get("proxy", "").strip() or get_global_proxy()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "bark":
        device_key = config.get("device_key", "")
        server_url = config.get("server_url", "").strip("/")
        if not device_key:
            raise ValueError("Bark 需要 device_key")
        if server_url:
            host = server_url.replace("https://", "").replace("http://", "")
            return f"bark://{host}/{device_key}/"
        return f"bark://{device_key}/"

    elif channel_type == "dingtalk":
        # Apprise 釘釘格式：
        # - 無加簽：dingtalk://{access_token}/
        # - 加簽：  dingtalk://{secret}@{access_token}/
        # - @手機號：在 URL 末尾追加 ?to=13800138000,13900139000
        token = (config.get("token") or "").strip()
        secret = (config.get("secret") or "").strip()
        phones = (config.get("phones") or "").strip()
        if not token:
            raise ValueError("釘釘需要 token")
        base = f"dingtalk://{secret}@{token}/" if secret else f"dingtalk://{token}/"
        if phones:
            # 僅保留數字和逗號
            phone_list = [
                re.sub(r"[^0-9]", "", p)
                for p in phones.split(",")
                if re.sub(r"[^0-9]", "", p)
            ]
            if phone_list:
                base += f"?to={','.join(phone_list)}"
        return base

    elif channel_type == "lark":
        webhook_token = config.get("webhook_token", "")
        if not webhook_token:
            raise ValueError("飛書需要 webhook_token")
        return f"lark://{webhook_token}/"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord 需要 webhook_id 和 webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover 需要 user_key 和 app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"不支援的 Apprise 管道型別: {channel_type}")


class NotifierManager:
    """通知管理器: Apprise 管道 + 自定義管道"""

    def __init__(self, policy=None):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0
        # 釘釘關鍵字（可選）：若群機器人啟用“關鍵字”安全校驗，則自動附加
        self._dingtalk_keywords: set[str] = set()
        self.policy = policy

    def add_channel(self, channel_type: str, config: dict):
        """新增通知管道"""
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # 需要自定義實現（如帶代理的 Telegram）
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"註冊自定義通知管道: {channel_type} (帶代理)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"註冊通知管道: {channel_type}")
                else:
                    logger.error(f"註冊通知管道失敗: {channel_type} (URL 無效)")
                if channel_type == "dingtalk":
                    kw = (config.get("keyword") or "").strip()
                    if kw:
                        self._dingtalk_keywords.add(kw)
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"註冊自定義通知管道: {channel_type}")
        except ValueError as e:
            logger.error(f"註冊通知管道失敗: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """向所有已註冊管道傳送通知（忽略錯誤）"""
        await self.notify_with_result(title, content, images)

    async def notify_with_result(
        self,
        title: str,
        content: str,
        images: list[str] | None = None,
        *,
        bypass_quiet_hours: bool = False,
    ) -> dict:
        """向所有已註冊管道傳送通知，返回結果"""
        if self._channel_count == 0:
            logger.warning("沒有可用的通知管道")
            return {"success": False, "error": "沒有可用的通知管道"}

        # Quiet hours
        try:
            if not bypass_quiet_hours and getattr(self, "policy", None):
                if self.policy.is_quiet_now():
                    logger.info("當前處於通知靜默時段，跳過傳送")
                    return {"success": False, "skipped": "quiet_hours"}
        except Exception:
            # do not block sends on policy errors
            pass

        # 準備純文本版本（用於不支援 Markdown 的管道）
        plain_content = sanitize_for_telegram(content)

        # 準備附件
        attachments = None
        if images:
            attachments = apprise.AppriseAttachment()
            for img_path in images:
                if img_path and os.path.exists(img_path):
                    attachments.add(img_path)

        errors = []

        # 若配置了釘釘關鍵字，自動追加在內容末尾以透過“關鍵字”校驗
        if self._dingtalk_keywords:
            suffix = " " + " ".join(sorted(self._dingtalk_keywords))
            if suffix.strip() not in plain_content:
                plain_content = (plain_content + "\n" + suffix).strip()
            if suffix.strip() not in content:
                content = (content + "\n" + suffix).strip()

        retry_attempts = 0
        backoff = 0.0
        try:
            if getattr(self, "policy", None):
                retry_attempts = max(0, int(self.policy.retry_attempts))
                backoff = float(self.policy.retry_backoff_seconds or 0.0)
        except Exception:
            retry_attempts = 0
            backoff = 0.0

        async def _sleep_retry(i: int):
            if backoff <= 0:
                return
            await asyncio.sleep(backoff * (2 ** max(0, i - 1)))

        # Apprise 管道（使用純文本，因為 Telegram 等不支援 Markdown）
        if len(self._ap) > 0:
            apprise_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    success = await self._ap.async_notify(
                        title=title,
                        body=plain_content,
                        body_format=apprise.NotifyFormat.TEXT,
                        attach=attachments,
                    )
                    if success:
                        apprise_ok = True
                        logger.info(f"Apprise 通知傳送成功: {title}")
                        break
                    last_err = "Apprise 通知傳送失敗（可能是網路問題或配置錯誤）"
                    logger.error(f"{last_err}: {title}")
                except Exception as e:
                    last_err = f"Apprise 通知異常: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not apprise_ok:
                errors.append(last_err or "Apprise 通知傳送失敗")

        # 自定義管道（根據管道型別自動選擇格式）
        for ch_type, config in self._custom_channels:
            ch_ok = False
            last_err = ""
            for attempt in range(0, retry_attempts + 1):
                try:
                    # 支援 Markdown 的管道使用原始內容，否則使用純文本
                    ch_content = (
                        content if ch_type in _MARKDOWN_CHANNELS else plain_content
                    )
                    await self._send_custom(ch_type, config, title, ch_content)
                    ch_ok = True
                    break
                except Exception as e:
                    last_err = f"{ch_type} 傳送失敗: {e}"
                    logger.error(last_err)
                if attempt < retry_attempts:
                    await _sleep_retry(attempt + 1)
            if not ch_ok:
                errors.append(last_err or f"{ch_type} 傳送失敗")

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """傳送自定義管道通知"""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        elif ch_type == "wecom":
            await self._send_wecom(config, title, content)
        elif ch_type == "serverchan":
            await self._send_serverchan(config, title, content)
        elif ch_type == "pushplus":
            await self._send_pushplus(config, title, content)
        else:
            logger.warning(f"未知的自定義管道型別: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API（支援代理）

        Telegram 老 Markdown 解析很脆弱:
        - 不認 `**粗體**`(只認 `*粗體*`),GitHub 風格會導致 Can't find end of entity
        - 不認 `### 標題`(把 # 當普通字元,但 ### 後面可能被截斷)
        - 單條上限 4096 字元,超過會被截斷破壞實體
        傳送前做相容性預處理 + 截斷。
        """
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        # 管道級代理優先，否則使用全域性代理
        proxy = config.get("proxy", "").strip() or get_global_proxy()

        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # 用現成的 sanitize_for_telegram 把 markdown 完全剝成純文本,
        # 避免 `**粗體**` / `## 標題` / 未閉合實體導致 Telegram parse 失敗。
        # 標題外層手動加 `*...*` 讓其加粗(Telegram 老 Markdown 只認單星號)。
        safe_title = sanitize_for_telegram(title) if title else ""
        safe_content = sanitize_for_telegram(content)
        text = f"*{safe_title}*\n\n{safe_content}" if safe_title else safe_content
        # Telegram 單條上限 4096,留點 buffer 給末尾提示
        if len(text) > 3900:
            # 正文末尾若帶詳細資訊連結(經 sanitize 後已是裸 URL),直接截斷會把它砍掉 →
            # 使用者點不到。先抽出來,截斷正文後再拼回末尾。
            link_m = re.search(r"(https?://[^\s)]+)\s*$", text)
            if link_m:
                notice = f"\n\n…內容過長已截斷,完整報告 👉 {link_m.group(1)}"
            else:
                notice = "\n\n…內容過長已截斷,完整報告請在 PanWatch 檢視"
            text = text[: 3900 - len(notice)].rstrip() + notice
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # 配置代理
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram 使用代理: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API 錯誤: {data.get('description')}")
                logger.info(f"Telegram 通知傳送成功: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"連線代理失敗 ({proxy}): {e}")
            else:
                raise RuntimeError(f"無法連線 Telegram API（可能需要配置代理）: {e}")
        except httpx.TimeoutException:
            raise RuntimeError("請求超時（網路問題或代理配置錯誤）")
        except Exception as e:
            if (
                "ConnectError" in str(type(e).__name__)
                or "connection" in str(e).lower()
            ):
                if not proxy:
                    raise RuntimeError(f"網路連線失敗，建議配置代理: {e}")
            raise

    async def _send_wecom(self, config: dict, title: str, content: str):
        """企業微信機器人 Webhook"""
        key = config.get("webhook_key", "")
        if not key:
            raise ValueError("企業微信需要 webhook_key")

        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        text = f"## {title}\n\n{content}" if title else content
        payload = {"msgtype": "markdown", "markdown": {"content": text}}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"企業微信傳送失敗: {data.get('errmsg')}")
            logger.info(f"企業微信通知傳送成功: {title}")

    async def _send_serverchan(self, config: dict, title: str, content: str):
        """Server醬推送"""
        sendkey = config.get("sendkey", "")
        if not sendkey:
            raise ValueError("Server醬需要 sendkey")

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title or "通知", "desp": content}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Server醬傳送失敗: {data.get('message')}")
            logger.info(f"Server醬通知傳送成功: {title}")

    async def _send_pushplus(self, config: dict, title: str, content: str):
        """PushPlus 推送"""
        token = config.get("token", "")
        if not token:
            raise ValueError("PushPlus 需要 token")

        url = "https://www.pushplus.plus/send"
        payload = {
            "token": token,
            "title": title or "通知",
            "content": content,
            "template": "markdown",
        }
        topic = config.get("topic", "")
        if topic:
            payload["topic"] = topic

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 200:
                raise RuntimeError(f"PushPlus 傳送失敗: {data.get('msg')}")
            logger.info(f"PushPlus 通知傳送成功: {title}")
