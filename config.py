from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().with_name('.env')


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            raise ValueError(f'.env 第 {line_number} 行格式无效: {raw_line!r}')
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f'.env 第 {line_number} 行缺少键名')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _get_config_value(name: str, dotenv: dict[str, str], default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    return dotenv.get(name, default)


def _read_int(name: str, dotenv: dict[str, str], default: int, minimum: int = 1) -> int:
    raw = _get_config_value(name, dotenv)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f'配置项 {name} 必须是整数，当前值: {raw!r}') from exc
    return max(value, minimum)


@dataclass(slots=True)
class AppConfig:
    db_path: Path
    poll_interval_seconds: int
    http_timeout_seconds: int
    telegram_poll_timeout_seconds: int
    bilibili_cookie: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    dome_webhook_url: str | None
    web_host: str
    web_port: int
    web_admin_password: str | None

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> 'AppConfig':
        dotenv = _load_dotenv(Path(env_file) if env_file is not None else DEFAULT_ENV_FILE)
        db_path = Path(_get_config_value('DB_PATH', dotenv, 'data/app.db') or 'data/app.db').expanduser()
        web_host = (_get_config_value('WEB_HOST', dotenv, '0.0.0.0') or '').strip() or '0.0.0.0'
        return cls(
            db_path=db_path,
            poll_interval_seconds=_read_int('POLL_INTERVAL_SECONDS', dotenv, 1800, minimum=30),
            http_timeout_seconds=_read_int('HTTP_TIMEOUT_SECONDS', dotenv, 20, minimum=5),
            telegram_poll_timeout_seconds=_read_int('TELEGRAM_POLL_TIMEOUT_SECONDS', dotenv, 2, minimum=1),
            bilibili_cookie=_get_config_value('BILIBILI_COOKIE', dotenv) or None,
            telegram_bot_token=_get_config_value('TELEGRAM_BOT_TOKEN', dotenv) or None,
            telegram_chat_id=_get_config_value('TELEGRAM_CHAT_ID', dotenv) or None,
            dome_webhook_url=_get_config_value('DOME_WEBHOOK_URL', dotenv) or None,
            web_host=web_host,
            web_port=_read_int('WEB_PORT', dotenv, 8688, minimum=1),
            web_admin_password=_get_config_value('WEB_ADMIN_PASSWORD', dotenv) or None,
        )