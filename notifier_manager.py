from __future__ import annotations

import logging
from typing import Any

from api.base import BaseNotifier
from db import Store

logger = logging.getLogger(__name__)


class NotifierManager:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._notifiers: dict[str, BaseNotifier] = {}

    def register(self, notifier: BaseNotifier, enabled_default: bool = False) -> None:
        self._notifiers[notifier.key] = notifier
        self.store.ensure_notifier(notifier.key, notifier.display_name, enabled_default)

    def list_statuses(self) -> list[dict[str, Any]]:
        return self.store.list_notifiers()

    def is_enabled(self, key: str) -> bool:
        return bool(self.store.get_notifier(key)["enabled"])

    def set_enabled(self, key: str, enabled: bool) -> dict[str, Any]:
        return self.store.set_notifier_enabled(key, enabled)

    def send_text(self, text: str, *, only_keys: list[str] | None = None) -> dict[str, bool]:
        targets = self._iter_targets(only_keys)
        results: dict[str, bool] = {}
        for key, notifier in targets:
            try:
                results[key] = notifier.send_text(text)
            except Exception:
                logger.exception("通知器 %s 发送文本失败", key)
                results[key] = False
        return results

    def broadcast_episode_update(self, show: dict[str, Any], episode: dict[str, Any]) -> dict[str, bool]:
        targets = self._iter_targets()
        results: dict[str, bool] = {}
        for key, notifier in targets:
            try:
                results[key] = notifier.send_episode_update(show, episode)
            except Exception:
                logger.exception("通知器 %s 发送番剧更新失败", key)
                results[key] = False
        return results

    def _iter_targets(self, only_keys: list[str] | None = None) -> list[tuple[str, BaseNotifier]]:
        statuses = {item["key"]: item for item in self.store.list_notifiers()}
        keys = only_keys or [key for key, info in statuses.items() if info["enabled"]]
        targets: list[tuple[str, BaseNotifier]] = []
        for key in keys:
            notifier = self._notifiers.get(key)
            if notifier is None:
                continue
            targets.append((key, notifier))
        return targets
