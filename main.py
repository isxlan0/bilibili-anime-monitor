from __future__ import annotations

import logging
import time

from api.dome import DomeNotifier
from api.tg import TelegramNotifier
from bilibili import BilibiliClient
from config import AppConfig
from db import Store
from notifier_manager import NotifierManager
from services import Poller, ShowService
from settings_service import SettingsService
from webapp import AdminWebServer, WebAdminApp


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)

    bootstrap = AppConfig.from_env()
    store = Store(bootstrap.db_path)
    store.initialize()
    settings = SettingsService(store, bootstrap)
    settings.initialize()
    admin_password = settings.ensure_admin_password()

    bilibili = BilibiliClient(bootstrap, settings)
    notifier_manager = NotifierManager(store)
    dome_notifier = DomeNotifier(bootstrap, settings)
    tg_notifier = TelegramNotifier(bootstrap, store, settings)

    notifier_manager.register(dome_notifier, enabled_default=bool(settings.get_dome_webhook_url()))
    notifier_manager.register(tg_notifier, enabled_default=bool(settings.get_telegram_token()))

    show_service = ShowService(store, bilibili)
    poller = Poller(store, bilibili, notifier_manager)
    web_app = WebAdminApp(settings, show_service, poller, notifier_manager)
    web_server = AdminWebServer(settings.get_web_host(), settings.get_web_port(), web_app)
    web_server.start()

    logger.info("番剧通知工具已启动")
    logger.info("Web 后台：http://%s:%s", settings.get_web_host(), settings.get_web_port())
    if admin_password.generated:
        logger.warning("============================================================")
        logger.warning("已自动生成 Web 后台初始密码（仅本次启动显示一次）")
        logger.warning("登录地址：http://%s:%s/login", settings.get_web_host(), settings.get_web_port())
        logger.warning("初始密码：%s", admin_password.password)
        logger.warning("请登录后立即在“通知与设置”中修改后台密码")
        logger.warning("若丢失此密码，可删除 settings 中的 web.admin_password 后重启")
        logger.warning("============================================================")
    if not settings.get_telegram_token():
        logger.info("未配置 Telegram Bot Token，Telegram 菜单不可用")

    last_poll_at = 0.0
    try:
        while True:
            tg_notifier.process_updates(show_service, poller, notifier_manager)
            now = time.monotonic()
            if now - last_poll_at >= settings.get_poll_interval_seconds():
                summary = poller.check_all()
                logger.info(
                    "轮询完成：检查 %s 部，新增 %s 集，错误 %s 个",
                    summary.checked_count,
                    summary.new_episode_count,
                    len(summary.errors),
                )
                last_poll_at = now
            time.sleep(0.25)
    except KeyboardInterrupt:
        logger.info("收到退出信号，正在关闭")
    finally:
        web_server.stop()
        store.close()


if __name__ == "__main__":
    main()
