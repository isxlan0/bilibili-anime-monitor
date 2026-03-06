import os
import tempfile
import unittest
from pathlib import Path

from config import AppConfig


class AppConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_from_env_reads_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / '.env'
            env_file.write_text(
                '\n'.join(
                    [
                        'TELEGRAM_BOT_TOKEN=test-token',
                        'TELEGRAM_CHAT_ID=123456',
                        'WEB_HOST=127.0.0.1',
                        'WEB_PORT=9000',
                        'DB_PATH=~/bangumi-test.db',
                    ]
                ),
                encoding='utf-8',
            )

            config = AppConfig.from_env(env_file)

        self.assertEqual(config.telegram_bot_token, 'test-token')
        self.assertEqual(config.telegram_chat_id, '123456')
        self.assertEqual(config.web_host, '127.0.0.1')
        self.assertEqual(config.web_port, 9000)
        self.assertEqual(config.db_path, Path('~/bangumi-test.db').expanduser())

    def test_os_environment_overrides_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / '.env'
            env_file.write_text('WEB_HOST=0.0.0.0\nWEB_PORT=8688\n', encoding='utf-8')
            os.environ['WEB_HOST'] = '127.0.0.1'
            os.environ['WEB_PORT'] = '9001'

            config = AppConfig.from_env(env_file)

        self.assertEqual(config.web_host, '127.0.0.1')
        self.assertEqual(config.web_port, 9001)