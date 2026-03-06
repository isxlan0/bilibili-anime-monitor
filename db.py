from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS shows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    season_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    added_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    show_id INTEGER NOT NULL,
                    episode_id TEXT NOT NULL,
                    episode_no TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    sort_index INTEGER NOT NULL,
                    publish_time TEXT,
                    is_notified INTEGER NOT NULL DEFAULT 0,
                    discovered_at TEXT NOT NULL,
                    notified_at TEXT,
                    UNIQUE(show_id, episode_id),
                    FOREIGN KEY(show_id) REFERENCES shows(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS notifiers (
                    key TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def ensure_notifier(self, key: str, display_name: str, enabled_default: bool) -> dict[str, Any]:
        with self._lock:
            current = self.conn.execute("SELECT * FROM notifiers WHERE key = ?", (key,)).fetchone()
            now = utc_now()
            if current is None:
                self.conn.execute(
                    "INSERT INTO notifiers (key, display_name, enabled, updated_at) VALUES (?, ?, ?, ?)",
                    (key, display_name, 1 if enabled_default else 0, now),
                )
            else:
                self.conn.execute(
                    "UPDATE notifiers SET display_name = ?, updated_at = ? WHERE key = ?",
                    (display_name, now, key),
                )
            self.conn.commit()
            return self.get_notifier(key)

    def get_notifier(self, key: str) -> dict[str, Any]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM notifiers WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise KeyError(f"未知通知器: {key}")
            return self._row_to_dict(row)

    def list_notifiers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM notifiers ORDER BY key").fetchall()
            return [self._row_to_dict(row) for row in rows]

    def set_notifier_enabled(self, key: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self.conn.execute(
                "UPDATE notifiers SET enabled = ?, updated_at = ? WHERE key = ?",
                (1 if enabled else 0, utc_now(), key),
            )
            self.conn.commit()
            return self.get_notifier(key)

    def ensure_settings(self, defaults: dict[str, str]) -> None:
        with self._lock:
            now = utc_now()
            for key, value in defaults.items():
                current = self.conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
                if current is None:
                    self.conn.execute(
                        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                        (key, value, now),
                    )
            self.conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            self.conn.commit()

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            now = utc_now()
            self.conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self.conn.commit()

    def set_settings(self, values: dict[str, str]) -> None:
        with self._lock:
            now = utc_now()
            for key, value in values.items():
                self.conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, value, now),
                )
            self.conn.commit()

    def find_show_by_season_id(self, season_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                self._show_query(include_inactive=True) + " AND s.season_id = ? LIMIT 1",
                (season_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def get_show_by_id(self, show_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                self._show_query(include_inactive=True) + " AND s.id = ? LIMIT 1",
                (show_id,),
            ).fetchone()
            return self._row_to_dict(row) if row else None

    def upsert_show(self, season_id: str, title: str, source_url: str) -> tuple[dict[str, Any], bool]:
        with self._lock:
            existing = self.find_show_by_season_id(season_id)
            now = utc_now()
            if existing is None:
                self.conn.execute(
                    """
                    INSERT INTO shows (season_id, title, source_url, added_at, last_checked_at, last_error)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (season_id, title, source_url, now, now),
                )
                self.conn.commit()
                created = True
            else:
                self.conn.execute(
                    """
                    UPDATE shows
                    SET title = ?, source_url = ?, status = 'active', last_checked_at = ?
                    WHERE season_id = ?
                    """,
                    (title, source_url, now, season_id),
                )
                self.conn.commit()
                created = False
            updated = self.find_show_by_season_id(season_id)
            if updated is None:
                raise RuntimeError("写入番剧信息失败")
            return updated, created

    def list_shows(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            query = self._show_query(include_inactive=include_inactive) + " ORDER BY COALESCE(s.last_checked_at, s.added_at) DESC, s.id DESC"
            rows = self.conn.execute(query).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def set_show_status(self, show_id: int, status: str) -> dict[str, Any]:
        if status not in {"active", "disabled"}:
            raise ValueError("状态必须是 active 或 disabled")
        with self._lock:
            self.conn.execute("UPDATE shows SET status = ? WHERE id = ?", (status, show_id))
            self.conn.commit()
            show = self.get_show_by_id(show_id)
            if show is None:
                raise KeyError(f"未知番剧 ID: {show_id}")
            return show

    def delete_show(self, show_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM shows WHERE id = ?", (show_id,))
            self.conn.commit()

    def seed_episodes(self, show_id: int, episodes: Iterable[Any]) -> None:
        with self._lock:
            now = utc_now()
            with self.conn:
                for episode in episodes:
                    self.conn.execute(
                        """
                        INSERT OR IGNORE INTO episodes (
                            show_id, episode_id, episode_no, title, url, sort_index,
                            publish_time, is_notified, discovered_at, notified_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            show_id,
                            episode.episode_id,
                            episode.episode_no,
                            episode.title,
                            episode.url,
                            episode.sort_index,
                            episode.publish_time,
                            now,
                            now,
                        ),
                    )

    def insert_new_episodes(self, show_id: int, episodes: Iterable[Any]) -> list[dict[str, Any]]:
        with self._lock:
            discovered_at = utc_now()
            inserted: list[dict[str, Any]] = []
            with self.conn:
                for episode in episodes:
                    cursor = self.conn.execute(
                        """
                        INSERT OR IGNORE INTO episodes (
                            show_id, episode_id, episode_no, title, url, sort_index,
                            publish_time, is_notified, discovered_at, notified_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)
                        """,
                        (
                            show_id,
                            episode.episode_id,
                            episode.episode_no,
                            episode.title,
                            episode.url,
                            episode.sort_index,
                            episode.publish_time,
                            discovered_at,
                        ),
                    )
                    if cursor.rowcount:
                        row = self.conn.execute(
                            "SELECT * FROM episodes WHERE show_id = ? AND episode_id = ?",
                            (show_id, episode.episode_id),
                        ).fetchone()
                        if row:
                            inserted.append(self._row_to_dict(row))
            return inserted

    def mark_episode_notified(self, episode_row_id: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE episodes SET is_notified = 1, notified_at = ? WHERE id = ?",
                (utc_now(), episode_row_id),
            )
            self.conn.commit()

    def update_show_after_check(self, show_id: int, title: str, source_url: str, error: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE shows
                SET title = ?, source_url = ?, last_checked_at = ?, last_error = ?
                WHERE id = ?
                """,
                (title, source_url, utc_now(), error, show_id),
            )
            self.conn.commit()

    def count_cached_episodes(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM episodes").fetchone()
            return int(row["count"] if row else 0)

    def recent_episodes(self, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.conn.execute(
                """
                SELECT e.*, s.title AS show_title
                FROM episodes e
                JOIN shows s ON s.id = e.show_id
                ORDER BY COALESCE(e.discovered_at, e.notified_at) DESC, e.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._row_to_dict(row) for row in rows]

    def dashboard_stats(self) -> dict[str, int]:
        with self._lock:
            active = self.conn.execute("SELECT COUNT(*) AS count FROM shows WHERE status = 'active'").fetchone()
            disabled = self.conn.execute("SELECT COUNT(*) AS count FROM shows WHERE status = 'disabled'").fetchone()
            episodes = self.conn.execute("SELECT COUNT(*) AS count FROM episodes").fetchone()
            return {
                "active_shows": int(active["count"] if active else 0),
                "disabled_shows": int(disabled["count"] if disabled else 0),
                "cached_episodes": int(episodes["count"] if episodes else 0),
            }

    def _show_query(self, include_inactive: bool = False) -> str:
        base = (
            """
            SELECT
                s.*,
                COALESCE((SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id), 0) AS cached_episode_count,
                (SELECT e.episode_no || ' ' || e.title FROM episodes e WHERE e.show_id = s.id ORDER BY e.sort_index DESC, e.id DESC LIMIT 1) AS latest_episode_label
            FROM shows s
            WHERE 1 = 1
            """
        )
        if not include_inactive:
            base += " AND s.status = 'active'"
        return base

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        data = dict(row)
        if "enabled" in data:
            data["enabled"] = bool(data["enabled"])
        if "is_notified" in data:
            data["is_notified"] = bool(data["is_notified"])
        return data
