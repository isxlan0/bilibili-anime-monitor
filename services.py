from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bilibili import BilibiliClient
from db import Store
from models import ShowSnapshot
from notifier_manager import NotifierManager


def format_episode_label(episode_no: str, title: str) -> str:
    return f"{episode_no} {title}".strip()


@dataclass()
class PollSummary:
    checked_count: int = 0
    new_episode_count: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "手动检查完成。",
            f"已检查番剧：{self.checked_count}",
            f"发现新剧集：{self.new_episode_count}",
        ]
        if self.details:
            lines.append("更新详情：")
            lines.extend(f"- {item}" for item in self.details)
        if self.errors:
            lines.append("异常：")
            lines.extend(f"- {item}" for item in self.errors)
        return "\n".join(lines)

    def short_message(self) -> str:
        if self.errors and not self.details:
            return self.errors[0]
        if self.details:
            return self.details[0]
        return f"已检查 {self.checked_count} 部番剧，暂无更新。"


@dataclass()
class TestNotificationSummary:
    show_title: str
    episode_label: str
    results: dict[str, bool]

    @property
    def attempted_count(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for succeeded in self.results.values() if succeeded)

    @property
    def failed_keys(self) -> list[str]:
        return [key for key, succeeded in self.results.items() if not succeeded]

    def to_text(self) -> str:
        lines = [
            "测试通知已发送。",
            f"样本番剧：{self.show_title}",
            f"样本剧集：{self.episode_label}",
            f"已尝试通道：{self.attempted_count}",
            f"成功通道：{self.success_count}",
        ]
        if self.failed_keys:
            lines.append(f"失败通道：{', '.join(self.failed_keys)}")
        return "\n".join(lines)

    def short_message(self) -> str:
        message = (
            f"测试通知已向 {self.attempted_count} 个通道发送，"
            f"成功 {self.success_count} 个。样本：{self.show_title} / {self.episode_label}"
        )
        if self.failed_keys:
            message += f"；失败：{', '.join(self.failed_keys)}"
        return message


class ShowService:
    def __init__(self, store: Store, bilibili: BilibiliClient) -> None:
        self.store = store
        self.bilibili = bilibili

    def add_show(self, raw: str) -> dict[str, Any]:
        snapshot = self.bilibili.fetch_show(raw)
        return self.add_snapshot(snapshot)

    def add_snapshot(self, snapshot: ShowSnapshot) -> dict[str, Any]:
        existing = self.store.find_show_by_season_id(snapshot.season_id)
        if existing is not None:
            if existing["status"] != "active":
                existing = self.store.set_show_status(existing["id"], "active")
            return {
                "created": False,
                "show": existing,
                "cached_episode_count": existing["cached_episode_count"],
                "latest_episode_label": existing.get("latest_episode_label") or "暂无缓存剧集",
            }

        created_show, _ = self.store.upsert_show(snapshot.season_id, snapshot.title, snapshot.source_url)
        self.store.seed_episodes(created_show["id"], snapshot.episodes)
        latest = snapshot.episodes[-1] if snapshot.episodes else None
        return {
            "created": True,
            "show": created_show,
            "cached_episode_count": len(snapshot.episodes),
            "latest_episode_label": format_episode_label(latest.episode_no, latest.title) if latest else "暂无缓存剧集",
        }

    def list_shows(self, include_inactive: bool = True) -> list[dict[str, Any]]:
        return self.store.list_shows(include_inactive=include_inactive)

    def render_show_list(self) -> str:
        shows = self.store.list_shows(include_inactive=True)
        if not shows:
            return "当前没有追踪中的番剧。"
        lines = ["当前追踪列表："]
        for index, show in enumerate(shows, start=1):
            latest = show.get("latest_episode_label") or "暂无缓存剧集"
            status = "追踪中" if show["status"] == "active" else "已停用"
            lines.append(
                f"{index}. 《{show['title']}》 | {status} | 已缓存 {show['cached_episode_count']} 集 | 最近：{latest}"
            )
        return "\n".join(lines)

    def set_show_status(self, show_id: int, enabled: bool) -> dict[str, Any]:
        return self.store.set_show_status(show_id, "active" if enabled else "disabled")

    def delete_show(self, show_id: int) -> None:
        self.store.delete_show(show_id)

    def get_latest_cached_episode(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        recent_episodes = self.store.recent_episodes(limit=1)
        if not recent_episodes:
            return None
        episode = recent_episodes[0]
        show = self.store.get_show_by_id(int(episode["show_id"]))
        if show is None:
            raise RuntimeError("测试通知样本对应的番剧记录不存在")
        return show, episode

    def send_test_notification(self, notifier_manager: NotifierManager) -> TestNotificationSummary:
        latest = self.get_latest_cached_episode()
        if latest is None:
            raise RuntimeError("暂无可用于测试的缓存剧集，请先添加并缓存至少一部番剧。")

        show, episode = latest
        show_payload = dict(show)
        show_payload["title"] = f"[测试通知] {show['title']}"
        results = notifier_manager.broadcast_episode_update(show_payload, episode)
        if not results:
            raise RuntimeError("当前没有已启用的通知通道，请先在通知与设置中开启至少一个通知器。")

        return TestNotificationSummary(
            show_title=show["title"],
            episode_label=format_episode_label(episode["episode_no"], episode["title"]),
            results=results,
        )


class Poller:
    def __init__(self, store: Store, bilibili: BilibiliClient, notifier_manager: NotifierManager) -> None:
        self.store = store
        self.bilibili = bilibili
        self.notifier_manager = notifier_manager

    def check_all(self) -> PollSummary:
        summary = PollSummary()
        for show in self.store.list_shows(include_inactive=False):
            self._check_show_record(show, summary)
        return summary

    def check_show(self, show_id: int) -> PollSummary:
        show = self.store.get_show_by_id(show_id)
        if show is None:
            raise KeyError(f"未知番剧 ID: {show_id}")
        summary = PollSummary()
        self._check_show_record(show, summary)
        return summary

    def _check_show_record(self, show: dict[str, Any], summary: PollSummary) -> None:
        summary.checked_count += 1
        try:
            snapshot = self.bilibili.fetch_show_by_season_id(show["season_id"])
            new_episodes = self.store.insert_new_episodes(show["id"], snapshot.episodes)
            show_payload = dict(show)
            show_payload.update({"title": snapshot.title, "source_url": snapshot.source_url})
            for episode in new_episodes:
                self.notifier_manager.broadcast_episode_update(show_payload, episode)
                self.store.mark_episode_notified(episode["id"])
                summary.details.append(
                    f"《{snapshot.title}》更新到 {format_episode_label(episode['episode_no'], episode['title'])}"
                )
            self.store.update_show_after_check(show["id"], snapshot.title, snapshot.source_url, error=None)
            summary.new_episode_count += len(new_episodes)
        except Exception as exc:
            summary.errors.append(f"《{show['title']}》: {exc}")
            self.store.update_show_after_check(show["id"], show["title"], show["source_url"], error=str(exc))
