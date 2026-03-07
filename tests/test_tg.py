import tempfile
import unittest
from pathlib import Path

from api.tg import TelegramNotifier
from config import AppConfig
from db import Store
from settings_service import SettingsService


class DummyNotifierManager:
    def list_statuses(self):
        return [{"key": "tg", "display_name": "Telegram", "enabled": True}]

    def is_enabled(self, key):
        return key == "tg"

    def set_enabled(self, key, enabled):
        return {"key": key, "display_name": "Telegram", "enabled": enabled}


class SpyTelegramNotifier(TelegramNotifier):
    def __init__(self, config, store, settings):
        super().__init__(config, store, settings)
        self.sent_messages = []
        self.answered_callbacks = []

    def send_text(self, text: str, **kwargs):
        self.sent_messages.append((text, kwargs))
        return True

    def _answer_callback(self, callback_query_id: str, text: str) -> None:
        self.answered_callbacks.append((callback_query_id, text))


class GuardedValue:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected access: {name}")


class TelegramNotifierAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = Store(db_path)
        self.store.initialize()
        config = AppConfig(
            db_path=db_path,
            poll_interval_seconds=1800,
            http_timeout_seconds=20,
            telegram_poll_timeout_seconds=2,
            bilibili_cookie=None,
            telegram_bot_token="bootstrap-token",
            telegram_chat_id=None,
            dome_webhook_url=None,
            web_host="0.0.0.0",
            web_port=8688,
            web_admin_password="secret-pass",
        )
        self.settings = SettingsService(self.store, config)
        self.settings.initialize()
        self.notifier = SpyTelegramNotifier(config, self.store, self.settings)
        self.notifier_manager = DummyNotifierManager()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_start_binds_first_private_chat(self) -> None:
        self.notifier._handle_message(
            {"chat": {"id": 10001}, "text": "/start"},
            GuardedValue(),
            GuardedValue(),
            self.notifier_manager,
        )

        self.assertEqual(self.settings.get_telegram_chat_id(), "10001")
        self.assertEqual(self.notifier.sent_messages[0][0], "已绑定当前聊天。使用 /menu 打开追番管理菜单。")
        self.assertEqual(self.notifier.sent_messages[0][1]["chat_id"], 10001)

    def test_unauthorized_private_message_cannot_override_bound_chat(self) -> None:
        self.settings.set("telegram.chat_id", "10001")

        self.notifier._handle_message(
            {"chat": {"id": 20002}, "text": "/menu"},
            GuardedValue(),
            GuardedValue(),
            self.notifier_manager,
        )

        self.assertEqual(self.settings.get_telegram_chat_id(), "10001")
        self.assertEqual(self.notifier.sent_messages, [("当前聊天无权操作此机器人。", {"chat_id": 20002})])

    def test_unauthorized_callback_cannot_trigger_menu_actions(self) -> None:
        self.settings.set("telegram.chat_id", "10001")

        self.notifier._handle_callback(
            {
                "id": "callback-1",
                "data": "menu:check_now",
                "message": {"chat": {"id": 20002}},
                "from": {"id": 20002},
            },
            GuardedValue(),
            GuardedValue(),
            self.notifier_manager,
        )

        self.assertEqual(self.settings.get_telegram_chat_id(), "10001")
        self.assertEqual(self.notifier.answered_callbacks, [("callback-1", "当前聊天无权操作此机器人")])
        self.assertEqual(self.notifier.sent_messages, [("当前聊天无权操作此机器人。", {"chat_id": 20002})])


if __name__ == "__main__":
    unittest.main()
