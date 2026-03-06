from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import AppConfig
from models import EpisodeInfo, ShowSnapshot
from settings_service import SettingsService


SS_OR_EP_PATTERN = re.compile(r"(?P<prefix>ss|ep)(?P<id>\d+)", re.IGNORECASE)


def extract_bangumi_identifier(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if not text:
        raise ValueError("请输入 ss/ep 编号或完整番剧链接")
    match = SS_OR_EP_PATTERN.search(text)
    if match:
        return match.group("prefix").lower(), match.group("id")
    if text.isdigit():
        return "ss", text
    raise ValueError("只支持 ss、ep 或包含它们的番剧播放链接")


class BilibiliClient:
    season_api = "https://api.bilibili.com/pgc/view/web/season"

    def __init__(self, config: AppConfig, settings: SettingsService) -> None:
        self.timeout = config.http_timeout_seconds
        self.settings = settings
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )

    def fetch_show(self, raw: str) -> ShowSnapshot:
        identifier_type, identifier = extract_bangumi_identifier(raw)
        if identifier_type == "ss":
            return self.fetch_show_by_season_id(identifier)
        return self._fetch_show_from_api({"ep_id": identifier})

    def fetch_show_by_season_id(self, season_id: str) -> ShowSnapshot:
        return self._fetch_show_from_api({"season_id": season_id})

    def _fetch_show_from_api(self, params: dict[str, str]) -> ShowSnapshot:
        try:
            payload = self._request_json(self.season_api, params)
            code = payload.get("code")
            if code != 0 or "result" not in payload:
                raise RuntimeError(f"B 站接口返回异常: code={code}, message={payload.get('message')}")
            return self._parse_season_payload(payload["result"])
        except Exception:
            return self._fetch_show_from_html(params)

    def _fetch_show_from_html(self, params: dict[str, str]) -> ShowSnapshot:
        if "season_id" in params:
            page_url = f"https://www.bilibili.com/bangumi/play/ss{params['season_id']}"
        else:
            page_url = f"https://www.bilibili.com/bangumi/play/ep{params['ep_id']}"
        html = self._request_text(page_url)
        state = self._extract_initial_state(html)
        media_info = state.get("mediaInfo") or {}
        season_id = str(media_info.get("season_id") or params.get("season_id") or "")
        title = media_info.get("title") or state.get("h1Title") or f"ss{season_id}"
        source_url = f"https://www.bilibili.com/bangumi/play/ss{season_id}" if season_id else page_url
        raw_episodes = state.get("epList") or []
        episodes = [self._parse_html_episode(item, index) for index, item in enumerate(raw_episodes, start=1)]
        return ShowSnapshot(
            season_id=season_id,
            title=title,
            source_url=source_url,
            episodes=episodes,
        )

    def _parse_season_payload(self, payload: dict[str, Any]) -> ShowSnapshot:
        season_id = str(payload["season_id"])
        title = payload.get("title") or payload.get("season_title") or f"ss{season_id}"
        source_url = payload.get("link") or f"https://www.bilibili.com/bangumi/play/ss{season_id}"
        raw_episodes = payload.get("episodes") or []
        episodes = [self._parse_episode(episode, index) for index, episode in enumerate(raw_episodes, start=1)]
        return ShowSnapshot(season_id=season_id, title=title, source_url=source_url, episodes=episodes)

    def _parse_episode(self, payload: dict[str, Any], sort_index: int) -> EpisodeInfo:
        raw_title = str(payload.get("title") or sort_index)
        long_title = (payload.get("long_title") or "").strip()
        show_title = (payload.get("show_title") or "").strip()
        episode_no = self._format_episode_no(raw_title, show_title)
        title = long_title or self._extract_title_from_show_title(show_title) or raw_title
        episode_id = str(payload.get("ep_id") or payload.get("id"))
        url = payload.get("link") or payload.get("share_url") or f"https://www.bilibili.com/bangumi/play/ep{episode_id}"
        publish_time = self._format_publish_time(payload.get("pub_time"))
        return EpisodeInfo(
            episode_id=episode_id,
            episode_no=episode_no,
            title=title,
            url=url,
            sort_index=sort_index,
            publish_time=publish_time,
        )

    def _parse_html_episode(self, payload: dict[str, Any], sort_index: int) -> EpisodeInfo:
        raw_title = str(payload.get("title") or payload.get("titleFormat") or sort_index)
        long_title = (payload.get("long_title") or payload.get("longTitle") or "").strip()
        show_title = (payload.get("show_title") or payload.get("showTitle") or f"{payload.get('titleFormat', '')} {long_title}").strip()
        episode_no = self._format_episode_no(raw_title, show_title)
        title = long_title or self._extract_title_from_show_title(show_title) or raw_title
        episode_id = str(payload.get("ep_id") or payload.get("id"))
        url = payload.get("link") or f"https://www.bilibili.com/bangumi/play/ep{episode_id}"
        publish_time = self._format_publish_time(payload.get("pub_time") or payload.get("pubTime"))
        return EpisodeInfo(
            episode_id=episode_id,
            episode_no=episode_no,
            title=title,
            url=url,
            sort_index=sort_index,
            publish_time=publish_time,
        )

    def _request_json(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        request_url = f"{url}?{urlencode(params)}"
        request = Request(request_url, headers=self._build_headers(accept="application/json, text/plain, */*"))
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"请求 B 站接口失败: HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"请求 B 站接口失败: {exc.reason}") from exc

    def _request_text(self, url: str) -> str:
        request = Request(
            url,
            headers=self._build_headers(accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            raise RuntimeError(f"请求番剧页面失败: HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"请求番剧页面失败: {exc.reason}") from exc

    def _build_headers(self, accept: str) -> dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Referer": "https://www.bilibili.com/",
        }
        cookie = self.settings.get_bilibili_cookie()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _extract_initial_state(self, html: str) -> dict[str, Any]:
        for marker in ("window.__INITIAL_STATE__=", "__INITIAL_STATE__="):
            start_marker = html.find(marker)
            if start_marker == -1:
                continue
            start = html.find("{", start_marker)
            if start == -1:
                continue
            brace_count = 0
            in_string = False
            escaped = False
            for index in range(start, len(html)):
                char = html[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        return json.loads(html[start:index + 1])
        raise RuntimeError("未能从番剧页面解析到初始化数据")

    @staticmethod
    def _extract_title_from_show_title(show_title: str) -> str:
        if not show_title:
            return ""
        parts = show_title.split(" ", 1)
        return parts[1].strip() if len(parts) > 1 else show_title

    @staticmethod
    def _format_episode_no(raw_title: str, show_title: str) -> str:
        if show_title.startswith("第"):
            head = show_title.split(" ", 1)[0].strip()
            if head:
                return head
        if raw_title.startswith("第"):
            return raw_title.split(" ", 1)[0].strip()
        if raw_title.isdigit():
            return f"第{raw_title}话"
        return raw_title

    @staticmethod
    def _format_publish_time(value: Any) -> str | None:
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")
        return None
