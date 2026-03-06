from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class EpisodeInfo:
    episode_id: str
    episode_no: str
    title: str
    url: str
    sort_index: int
    publish_time: str | None = None


@dataclass(slots=True, frozen=True)
class ShowSnapshot:
    season_id: str
    title: str
    source_url: str
    episodes: list[EpisodeInfo]
