import tempfile
import unittest
from pathlib import Path

from db import Store
from models import EpisodeInfo, ShowSnapshot
from services import Poller, ShowService


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


class FakeBilibiliClient:
    def __init__(self, initial_snapshot: ShowSnapshot) -> None:
        self.snapshot = initial_snapshot

    def fetch_show(self, raw: str) -> ShowSnapshot:
        return self.snapshot

    def fetch_show_by_season_id(self, season_id: str) -> ShowSnapshot:
        return self.snapshot


class FakeNotifierManager:
    def __init__(self) -> None:
        self.notifications = []

    def broadcast_episode_update(self, show, episode):
        self.notifications.append((show["title"], episode["episode_no"], episode["title"]))
        return {"fake": True}


class ServiceFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "app.db"
        self.store = Store(self.db_path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_add_show_seeds_without_notifications(self) -> None:
        client = FakeBilibiliClient(make_snapshot(2))
        service = ShowService(self.store, client)

        result = service.add_show("ss123")

        self.assertTrue(result["created"])
        self.assertEqual(result["cached_episode_count"], 2)
        self.assertEqual(result["latest_episode_label"], "第2话 标题 2")

    def test_poller_notifies_only_new_episodes(self) -> None:
        client = FakeBilibiliClient(make_snapshot(2))
        service = ShowService(self.store, client)
        service.add_show("ss123")
        notifier = FakeNotifierManager()
        poller = Poller(self.store, client, notifier)

        client.snapshot = make_snapshot(3)
        summary = poller.check_all()
        self.assertEqual(summary.new_episode_count, 1)
        self.assertEqual(len(notifier.notifications), 1)
        self.assertEqual(notifier.notifications[0], ("测试番剧", "第3话", "标题 3"))

        summary = poller.check_all()
        self.assertEqual(summary.new_episode_count, 0)
        self.assertEqual(len(notifier.notifications), 1)

    def test_send_test_notification_uses_latest_cached_episode(self) -> None:
        client = FakeBilibiliClient(make_snapshot(2))
        service = ShowService(self.store, client)
        service.add_show("ss123")
        notifier = FakeNotifierManager()

        summary = service.send_test_notification(notifier)

        self.assertEqual(summary.show_title, "测试番剧")
        self.assertEqual(summary.episode_label, "第2话 标题 2")
        self.assertEqual(summary.results, {"fake": True})
        self.assertEqual(notifier.notifications, [('[测试通知] 测试番剧', '第2话', '标题 2')])
