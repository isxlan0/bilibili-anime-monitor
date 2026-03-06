from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class BaseNotifier(ABC):
    key: str
    display_name: str

    @abstractmethod
    def send_text(self, text: str, **kwargs: Any) -> bool:
        raise NotImplementedError

    @abstractmethod
    def send_episode_update(self, show: Mapping[str, Any], episode: Mapping[str, Any]) -> bool:
        raise NotImplementedError
