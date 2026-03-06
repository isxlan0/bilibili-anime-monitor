import tempfile
import unittest
from pathlib import Path

from config import AppConfig
from db import Store
from settings_service import SettingsService


class SettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "app.db"
        self.store = Store(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_generates_admin_password_once_when_missing(self) -> None:
        settings = SettingsService(
            self.store,
            AppConfig(
                db_path=self.db_path,
                poll_interval_seconds=1800,
                http_timeout_seconds=20,
                telegram_poll_timeout_seconds=2,
                bilibili_cookie=None,
                telegram_bot_token=None,
                telegram_chat_id=None,
                dome_webhook_url=None,
                web_host="0.0.0.0",
                web_port=8688,
                web_admin_password=None,
            ),
        )
        settings.initialize()

        first = settings.ensure_admin_password()
        second = settings.ensure_admin_password()

        self.assertTrue(first.generated)
        self.assertFalse(second.generated)
        self.assertEqual(first.password, second.password)
        self.assertEqual(settings.get_admin_password(), first.password)

    def test_regenerates_after_admin_password_deleted(self) -> None:
        settings = SettingsService(
            self.store,
            AppConfig(
                db_path=self.db_path,
                poll_interval_seconds=1800,
                http_timeout_seconds=20,
                telegram_poll_timeout_seconds=2,
                bilibili_cookie=None,
                telegram_bot_token=None,
                telegram_chat_id=None,
                dome_webhook_url=None,
                web_host="0.0.0.0",
                web_port=8688,
                web_admin_password=None,
            ),
        )
        settings.initialize()
        first = settings.ensure_admin_password()

        self.store.delete_setting("web.admin_password")
        second = settings.ensure_admin_password()

        self.assertTrue(second.generated)
        self.assertNotEqual(first.password, second.password)
