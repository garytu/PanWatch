"""Authenticated, narrow client for twmd's durable quote-subscription control API."""

import httpx

from src.platform.runtime.config import Settings


class TwmdControlError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        self.status_code = status_code
        super().__init__(message)


class TwmdControlClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()

    def subscriptions(self, method: str = "GET", instrument_id: str | None = None) -> dict:
        token = self.settings.twmd_control_agent_token
        if not token:
            raise TwmdControlError("twmd 控制服務憑證尚未設定")
        path = "/api/v1/control/quote-subscriptions"
        if instrument_id is not None:
            path += f"/{instrument_id}"
        try:
            with httpx.Client(timeout=self.settings.twmd_timeout_sec, trust_env=False,
                              follow_redirects=False) as client:
                response = client.request(
                    method, self.settings.twmd_control_base_url.rstrip("/") + path,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status_code in (401, 403):
                raise TwmdControlError("twmd 控制服務拒絕憑證", 502)
            if response.status_code >= 400:
                raise TwmdControlError("twmd 訂閱要求失敗", 502)
            data = response.json().get("data")
            if not isinstance(data, dict) or not isinstance(data.get("instrument_ids"), list):
                raise TwmdControlError("twmd 訂閱回應格式不正確")
            return data
        except (httpx.RequestError, ValueError) as exc:
            raise TwmdControlError("twmd 控制服務暫時無法連線") from exc
