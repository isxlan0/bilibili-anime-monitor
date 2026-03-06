from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from typing import Any

from config import AppConfig
from db import Store


@dataclass(frozen=True)
class SettingsChangeResult:
    restart_required: bool
    updated_keys: tuple[str, ...]


@dataclass(frozen=True)
class AdminPasswordBootstrapResult:
    password: str
    generated: bool


class SettingsService:
    def __init__(self, store: Store, bootstrap: AppConfig) -> None:
        self.store = store
        self.bootstrap = bootstrap
        self.defaults = {
            "poll.interval_seconds": str(bootstrap.poll_interval_seconds),
            "telegram.bot_token": bootstrap.telegram_bot_token or "",
            "telegram.chat_id": bootstrap.telegram_chat_id or "",
            "dome.webhook_url": bootstrap.dome_webhook_url or "",
            "bilibili.cookie": bootstrap.bilibili_cookie or "",
            "web.host": bootstrap.web_host,
            "web.port": str(bootstrap.web_port),
        }
        if bootstrap.web_admin_password:
            self.defaults["web.admin_password"] = bootstrap.web_admin_password

    def initialize(self) -> None:
        self.store.ensure_settings(self.defaults)

    def ensure_admin_password(self) -> AdminPasswordBootstrapResult:
        existing = self.get_optional("web.admin_password")
        if existing:
            return AdminPasswordBootstrapResult(password=existing, generated=False)
        generated = self._generate_password()
        self.set("web.admin_password", generated)
        return AdminPasswordBootstrapResult(password=generated, generated=True)

    def get(self, key: str, fallback: str = "") -> str:
        default = self.defaults.get(key, fallback)
        value = self.store.get_setting(key, default)
        return value if value is not None else fallback

    def get_optional(self, key: str) -> str | None:
        value = self.get(key, "")
        return value or None

    def get_int(self, key: str, fallback: int) -> int:
        raw = self.get(key, str(fallback)).strip()
        try:
            value = int(raw)
        except ValueError:
            return fallback
        return max(value, 1)

    def set(self, key: str, value: str) -> None:
        self.store.set_setting(key, value)

    def set_many(self, values: dict[str, str]) -> None:
        self.store.set_settings(values)

    def get_poll_interval_seconds(self) -> int:
        return max(self.get_int("poll.interval_seconds", self.bootstrap.poll_interval_seconds), 30)

    def get_telegram_token(self) -> str | None:
        return self.get_optional("telegram.bot_token")

    def get_telegram_chat_id(self) -> str | None:
        return self.get_optional("telegram.chat_id")

    def get_dome_webhook_url(self) -> str | None:
        return self.get_optional("dome.webhook_url")

    def get_bilibili_cookie(self) -> str | None:
        return self.get_optional("bilibili.cookie")

    def get_web_host(self) -> str:
        return self.get("web.host", self.bootstrap.web_host)

    def get_web_port(self) -> int:
        return self.get_int("web.port", self.bootstrap.web_port)

    def get_admin_password(self) -> str | None:
        return self.get_optional("web.admin_password")

    def masked(self, key: str) -> str:
        value = self.get_optional(key)
        if not value:
            return "未设置"
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:3]}{'*' * max(3, len(value) - 5)}{value[-2:]}"

    def save_web_settings(self, form: dict[str, str]) -> SettingsChangeResult:
        updates: dict[str, str] = {}
        restart_required = False
        updated_keys: list[str] = []

        mapping = {
            "telegram_chat_id": "telegram.chat_id",
            "poll_interval_seconds": "poll.interval_seconds",
            "web_host": "web.host",
            "web_port": "web.port",
        }
        for form_key, setting_key in mapping.items():
            if form_key in form:
                value = form[form_key].strip()
                if not value:
                    continue
                if self.get(setting_key, "") != value:
                    updates[setting_key] = value
                    updated_keys.append(setting_key)
                    if setting_key in {"web.host", "web.port"}:
                        restart_required = True

        sensitive_mapping = {
            "telegram_bot_token": "telegram.bot_token",
            "dome_webhook_url": "dome.webhook_url",
            "bilibili_cookie": "bilibili.cookie",
            "web_admin_password": "web.admin_password",
        }
        for form_key, setting_key in sensitive_mapping.items():
            if form_key not in form:
                continue
            value = form[form_key].strip()
            if not value:
                continue
            if self.get(setting_key, "") != value:
                updates[setting_key] = value
                updated_keys.append(setting_key)

        if updates:
            self.set_many(updates)
        return SettingsChangeResult(restart_required=restart_required, updated_keys=tuple(updated_keys))

    def describe_runtime(self) -> dict[str, Any]:
        return {
            "web_host": self.get_web_host(),
            "web_port": self.get_web_port(),
            "poll_interval_seconds": self.get_poll_interval_seconds(),
            "telegram_bot_token_masked": self.masked("telegram.bot_token"),
            "telegram_chat_id": self.get("telegram.chat_id", ""),
            "dome_webhook_masked": self.masked("dome.webhook_url"),
            "bilibili_cookie_masked": self.masked("bilibili.cookie"),
            "admin_password_masked": self.masked("web.admin_password"),
            "admin_password_configured": bool(self.get_admin_password()),
        }

    @staticmethod
    def _generate_password(length: int = 20) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))
