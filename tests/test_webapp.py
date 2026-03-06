import io
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlparse
from wsgiref.util import setup_testing_defaults

from config import AppConfig
from db import Store
from models import EpisodeInfo, ShowSnapshot
from notifier_manager import NotifierManager
from services import Poller, ShowService
from settings_service import SettingsService
from webapp import WebAdminApp


class FakeBilibiliClient:
    def __init__(self, initial_snapshot: ShowSnapshot) -> None:
        self.snapshot = initial_snapshot

    def fetch_show(self, raw: str) -> ShowSnapshot:
        return self.snapshot

    def fetch_show_by_season_id(self, season_id: str) -> ShowSnapshot:
        return self.snapshot


class DummyNotifier:
    def __init__(self, key: str, name: str) -> None:
        self.key = key
        self.display_name = name
        self.text_messages = []
        self.episode_messages = []

    def send_text(self, text: str, **kwargs):
        self.text_messages.append((text, kwargs))
        return True

    def send_episode_update(self, show, episode):
        self.episode_messages.append((dict(show), dict(episode)))
        return True


def make_snapshot(episode_count: int) -> ShowSnapshot:
    episodes = []
    for index in range(1, episode_count + 1):
        episodes.append(
            EpisodeInfo(
                episode_id=str(1000 + index),
                episode_no=f"第{index}话",
                title=f"标题 {index}",
                url=f"https://www.bilibili.com/bangumi/play/ep{1000 + index}",
                sort_index=index,
            )
        )
    return ShowSnapshot(
        season_id="123",
        title="测试番剧",
        source_url="https://www.bilibili.com/bangumi/play/ss123",
        episodes=episodes,
    )


def run_wsgi(app, method="GET", path="/", form=None, cookie=None):
    parsed = urlparse(path)
    environ = {}
    setup_testing_defaults(environ)
    body = urlencode(form or {}).encode("utf-8")
    environ["REQUEST_METHOD"] = method
    environ["PATH_INFO"] = parsed.path
    environ["QUERY_STRING"] = parsed.query
    environ["wsgi.input"] = io.BytesIO(body)
    environ["CONTENT_LENGTH"] = str(len(body))
    environ["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    response_body = b"".join(app(environ, start_response))
    return captured, response_body


class WebAdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "app.db"
        self.store = Store(db_path)
        self.store.initialize()
        bootstrap = AppConfig(
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
        self.settings = SettingsService(self.store, bootstrap)
        self.settings.initialize()
        self.notifier_manager = NotifierManager(self.store)
        self.tg_notifier = DummyNotifier("tg", "Telegram")
        self.dome_notifier = DummyNotifier("dome", "Dome Webhook")
        self.notifier_manager.register(self.tg_notifier, enabled_default=True)
        self.notifier_manager.register(self.dome_notifier, enabled_default=False)
        client = FakeBilibiliClient(make_snapshot(2))
        self.show_service = ShowService(self.store, client)
        self.poller = Poller(self.store, client, self.notifier_manager)
        self.app = WebAdminApp(self.settings, self.show_service, self.poller, self.notifier_manager)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _login(self) -> str:
        captured, _ = run_wsgi(self.app, method="POST", path="/login", form={"password": "secret-pass"})
        header_map = dict(captured["headers"])
        return header_map["Set-Cookie"]

    def test_redirects_to_login_when_not_authenticated(self) -> None:
        captured, _ = run_wsgi(self.app, method="GET", path="/")
        self.assertEqual(captured["status"], "303 See Other")
        self.assertEqual(dict(captured["headers"])["Location"], "/login")

    def test_login_sets_session_cookie(self) -> None:
        captured, _ = run_wsgi(self.app, method="POST", path="/login", form={"password": "secret-pass"})
        self.assertEqual(captured["status"], "303 See Other")
        headers = dict(captured["headers"])
        self.assertIn("bangumi_admin_session", headers["Set-Cookie"])

    def test_settings_update_persists(self) -> None:
        cookie = self._login()
        captured, _ = run_wsgi(
            self.app,
            method="POST",
            path="/settings",
            cookie=cookie,
            form={
                "telegram_chat_id": "123456",
                "poll_interval_seconds": "600",
                "web_host": "0.0.0.0",
                "web_port": "8688",
                "enable_tg": "on",
            },
        )
        self.assertEqual(captured["status"], "303 See Other")
        self.assertEqual(self.settings.get_telegram_chat_id(), "123456")
        self.assertEqual(self.settings.get_poll_interval_seconds(), 600)

    def test_settings_page_uses_dedicated_secondary_forms(self) -> None:
        cookie = self._login()

        captured, body = run_wsgi(self.app, method="GET", path="/settings", cookie=cookie)

        self.assertEqual(captured["status"], "200 OK")
        html = body.decode("utf-8")
        self.assertIn("<form method='post' action='/settings' class='settings-primary-form'>", html)
        self.assertIn("<div class='settings-grid'>", html)
        self.assertIn("<form method='post' action='/settings/password' class='panel settings-card-form'>", html)
        self.assertIn("<form method='post' action='/settings/test-notification' class='panel settings-card-form'>", html)
        self.assertNotIn("formaction='/settings/test-notification'", html)

    def test_password_update_uses_dedicated_route(self) -> None:
        cookie = self._login()

        captured, _ = run_wsgi(
            self.app,
            method="POST",
            path="/settings/password",
            cookie=cookie,
            form={
                "web_admin_password": "new-secret-pass",
                "web_admin_password_confirm": "new-secret-pass",
            },
        )

        self.assertEqual(captured["status"], "303 See Other")
        self.assertEqual(self.settings.get_admin_password(), "new-secret-pass")

    def test_favicon_returns_empty_response(self) -> None:
        captured, body = run_wsgi(self.app, method="GET", path="/favicon.ico")

        self.assertEqual(captured["status"], "204 No Content")
        self.assertEqual(body, b"")

    def test_add_show_from_web(self) -> None:
        cookie = self._login()
        captured, _ = run_wsgi(
            self.app,
            method="POST",
            path="/shows",
            cookie=cookie,
            form={"show_input": "ss123"},
        )
        self.assertEqual(captured["status"], "303 See Other")
        shows = self.show_service.list_shows(include_inactive=True)
        self.assertEqual(len(shows), 1)
        self.assertEqual(shows[0]["title"], "测试番剧")

    def test_test_notification_sends_latest_episode_to_enabled_channel(self) -> None:
        self.show_service.add_show("ss123")
        cookie = self._login()

        captured, _ = run_wsgi(
            self.app,
            method="POST",
            path="/settings/test-notification",
            cookie=cookie,
        )

        self.assertEqual(captured["status"], "303 See Other")
        self.assertEqual(len(self.tg_notifier.episode_messages), 1)
        show, episode = self.tg_notifier.episode_messages[0]
        self.assertEqual(show["title"], "[测试通知] 测试番剧")
        self.assertEqual(episode["episode_no"], "第2话")
        self.assertEqual(episode["title"], "标题 2")
        self.assertEqual(self.dome_notifier.episode_messages, [])
