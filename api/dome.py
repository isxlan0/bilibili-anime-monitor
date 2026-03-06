from __future__ import annotations

import json
import logging
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from api.base import BaseNotifier
from config import AppConfig
from db import utc_now
from settings_service import SettingsService

logger = logging.getLogger(__name__)


class DomeNotifier(BaseNotifier):
    key = "dome"
    display_name = "Dome Webhook"

    def __init__(self, config: AppConfig, settings: SettingsService) -> None:
        self.timeout = config.http_timeout_seconds
        self.settings = settings

    def send_text(self, text: str, **kwargs: Any) -> bool:
        payload = {
            "type": "text",
            "text": text,
            "sent_at": utc_now(),
        }
        return self._post(payload)

    def send_episode_update(self, show: Mapping[str, Any], episode: Mapping[str, Any]) -> bool:
        payload = {
            "type": "episode_update",
            "sent_at": utc_now(),
            "show": {
                "season_id": show["season_id"],
                "title": show["title"],
                "url": show["source_url"],
            },
            "episode": {
                "episode_id": episode["episode_id"],
                "episode_no": episode["episode_no"],
                "title": episode["title"],
                "url": episode["url"],
                "publish_time": episode.get("publish_time"),
            },
        }
        return self._post(payload)

    def _post(self, payload: dict[str, Any]) -> bool:
        webhook_url = self.settings.get_dome_webhook_url()
        if not webhook_url:
            logger.warning("DomeNotifier 未配置 webhook，跳过发送")
            return False
        request = Request(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return 200 <= response.status < 300
        except HTTPError as exc:
            logger.warning("DomeNotifier HTTP 错误: %s", exc.code)
            return False
        except URLError as exc:
            logger.warning("DomeNotifier 网络错误: %s", exc.reason)
            return False
