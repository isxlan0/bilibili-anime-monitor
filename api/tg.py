from __future__ import annotations

import json
import logging
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from api.base import BaseNotifier
from config import AppConfig
from settings_service import SettingsService
from services import Poller, ShowService

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    key = "tg"
    display_name = "Telegram"

    def __init__(self, config: AppConfig, store: Any, settings: SettingsService) -> None:
        self.store = store
        self.settings = settings
        self.timeout = config.http_timeout_seconds
        self.poll_timeout = config.telegram_poll_timeout_seconds
        self.pending_actions: dict[int, str] = {}

    def send_text(self, text: str, **kwargs: Any) -> bool:
        token = self.settings.get_telegram_token()
        if not token:
            logger.warning("Telegram Bot Token 未配置，跳过发送")
            return False
        chat_id = kwargs.get("chat_id") or self.settings.get_telegram_chat_id()
        if not chat_id:
            logger.warning("Telegram chat_id 未配置，跳过发送")
            return False
        payload: dict[str, Any] = {
            "chat_id": str(chat_id),
            "text": text,
        }
        reply_markup = kwargs.get("reply_markup")
        if reply_markup is not None:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self._call("sendMessage", payload, token)
        return True

    def send_episode_update(self, show: Mapping[str, Any], episode: Mapping[str, Any]) -> bool:
        lines = [
            "📺 番剧更新提醒",
            f"番剧：{show['title']}",
            f"剧集：{episode['episode_no']} {episode['title']}",
            f"链接：{episode['url']}",
        ]
        return self.send_text("\n".join(lines))

    def process_updates(self, show_service: ShowService, poller: Poller, notifier_manager: Any) -> None:
        token = self.settings.get_telegram_token()
        if not token:
            return
        offset = int(self.store.get_setting("telegram.update_offset", "0") or "0")
        try:
            response = self._call(
                "getUpdates",
                {
                    "timeout": str(self.poll_timeout),
                    "offset": str(offset),
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
                token,
            )
        except Exception:
            logger.exception("拉取 Telegram 更新失败")
            return
        for update in response.get("result", []):
            next_offset = int(update["update_id"]) + 1
            self.store.set_setting("telegram.update_offset", str(next_offset))
            try:
                self._handle_update(update, show_service, poller, notifier_manager)
            except Exception:
                logger.exception("处理 Telegram 更新失败: %s", update)

    def _handle_update(self, update: dict[str, Any], show_service: ShowService, poller: Poller, notifier_manager: Any) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"], show_service, poller, notifier_manager)
            return
        message = update.get("message") or update.get("edited_message")
        if message:
            self._handle_message(message, show_service, poller, notifier_manager)

    def _handle_message(self, message: dict[str, Any], show_service: ShowService, poller: Poller, notifier_manager: Any) -> None:
        chat_id = int(message["chat"]["id"])
        text = (message.get("text") or "").strip()
        command = self._extract_command(text)
        if command == "/start":
            bound_chat_id = self._get_bound_chat_id()
            if bound_chat_id is None:
                self.store.set_setting("telegram.chat_id", str(chat_id))
            elif bound_chat_id != chat_id:
                self.pending_actions.pop(chat_id, None)
                self.send_text("当前聊天无权操作此机器人。", chat_id=chat_id)
                return
            self.pending_actions.pop(chat_id, None)
            self.send_text("已绑定当前聊天。使用 /menu 打开追番管理菜单。", chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        if not self._ensure_authorized_chat(chat_id):
            return
        if command == "/menu":
            self.pending_actions.pop(chat_id, None)
            self._send_menu(chat_id, notifier_manager)
            return
        if self.pending_actions.get(chat_id) == "awaiting_show_url":
            self.pending_actions.pop(chat_id, None)
            try:
                result = show_service.add_show(text)
            except Exception as exc:
                self.send_text(f"添加失败：{exc}", chat_id=chat_id)
                self._send_menu(chat_id, notifier_manager)
                return
            lines = [
                f"已{'添加' if result['created'] else '识别到已存在'}《{result['show']['title']}》。",
                f"已缓存剧集：{result['cached_episode_count']}",
                f"最新剧集：{result['latest_episode_label']}",
            ]
            self.send_text("\n".join(lines), chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        if text:
            self.send_text("请发送 /menu 打开菜单，或先点击“添加番剧”。", chat_id=chat_id)

    def _handle_callback(self, callback_query: dict[str, Any], show_service: ShowService, poller: Poller, notifier_manager: Any) -> None:
        callback_id = callback_query["id"]
        data = callback_query.get("data", "")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id") or callback_query.get("from", {}).get("id"))
        if not self._ensure_authorized_chat(chat_id, callback_query_id=callback_id):
            return

        if data == "menu:add_show":
            self.pending_actions[chat_id] = "awaiting_show_url"
            self._answer_callback(callback_id, "请发送番剧链接")
            self.send_text("请发送 B 站番剧链接、ss 编号或 ep 编号。", chat_id=chat_id)
            return
        if data == "menu:list_shows":
            self._answer_callback(callback_id, "已返回追番列表")
            self.send_text(show_service.render_show_list(), chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        if data == "menu:check_now":
            self._answer_callback(callback_id, "正在检查")
            summary = poller.check_all()
            self.send_text(summary.to_text(), chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        if data == "menu:test_notify":
            self._answer_callback(callback_id, "正在发送测试通知")
            try:
                summary = show_service.send_test_notification(notifier_manager)
            except Exception as exc:
                self.send_text(f"测试通知发送失败：{exc}", chat_id=chat_id)
            else:
                self.send_text(summary.to_text(), chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        if data.startswith("menu:toggle:"):
            key = data.rsplit(":", 1)[-1]
            updated = notifier_manager.set_enabled(key, not notifier_manager.is_enabled(key))
            self._answer_callback(callback_id, f"{updated['display_name']} 已{'开启' if updated['enabled'] else '关闭'}")
            self.send_text(f"{updated['display_name']} 已{'开启' if updated['enabled'] else '关闭'}。", chat_id=chat_id)
            self._send_menu(chat_id, notifier_manager)
            return
        self._answer_callback(callback_id, "未知操作")

    def _send_menu(self, chat_id: int, notifier_manager: Any) -> None:
        keyboard = [
            [{"text": "➕ 添加番剧", "callback_data": "menu:add_show"}],
            [
                {"text": "📺 追番列表", "callback_data": "menu:list_shows"},
                {"text": "🔄 手动检查", "callback_data": "menu:check_now"},
            ],
            [{"text": "🧪 测试通知", "callback_data": "menu:test_notify"}],
        ]
        toggle_buttons = []
        for notifier in notifier_manager.list_statuses():
            flag = "✅" if notifier["enabled"] else "❌"
            toggle_buttons.append(
                {
                    "text": f"{flag} {notifier['display_name']}",
                    "callback_data": f"menu:toggle:{notifier['key']}",
                }
            )
        if toggle_buttons:
            keyboard.append(toggle_buttons)
        self.send_text("追番管理菜单：", chat_id=chat_id, reply_markup={"inline_keyboard": keyboard})

    def _answer_callback(self, callback_query_id: str, text: str) -> None:
        token = self.settings.get_telegram_token()
        if not token:
            return
        try:
            self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}, token)
        except Exception:
            logger.exception("响应 Telegram callback 失败")

    def _call(self, method_name: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
        data = urlencode(payload).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{token}/{method_name}",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(self.timeout, self.poll_timeout + 5)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Telegram API HTTP 错误: {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Telegram API 网络错误: {exc.reason}") from exc
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API 返回失败: {body}")
        return body

    @staticmethod
    def _extract_command(text: str) -> str | None:
        if not text.startswith("/"):
            return None
        command = text.split()[0]
        if "@" in command:
            command = command.split("@", 1)[0]
        return command

    def _get_bound_chat_id(self) -> int | None:
        raw_chat_id = self.settings.get_telegram_chat_id()
        if not raw_chat_id:
            return None
        try:
            return int(raw_chat_id)
        except ValueError:
            logger.warning("telegram.chat_id 配置无效: %s", raw_chat_id)
            return None

    def _ensure_authorized_chat(self, chat_id: int, callback_query_id: str | None = None) -> bool:
        bound_chat_id = self._get_bound_chat_id()
        if bound_chat_id is None:
            if callback_query_id:
                self._answer_callback(callback_query_id, "请先发送 /start 绑定当前聊天")
            self.send_text("请先发送 /start 绑定当前聊天。", chat_id=chat_id)
            return False
        if bound_chat_id != chat_id:
            self.pending_actions.pop(chat_id, None)
            if callback_query_id:
                self._answer_callback(callback_query_id, "当前聊天无权操作此机器人")
            self.send_text("当前聊天无权操作此机器人。", chat_id=chat_id)
            return False
        return True
