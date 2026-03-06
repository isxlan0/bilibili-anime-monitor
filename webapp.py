from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from http import cookies
from typing import Any
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import WSGIRequestHandler, make_server

from notifier_manager import NotifierManager
from services import Poller, ShowService
from settings_service import SettingsService

logger = logging.getLogger(__name__)
SESSION_COOKIE = "bangumi_admin_session"
SESSION_TTL_SECONDS = 24 * 60 * 60


@dataclass()
class Response:
    status: str
    headers: list[tuple[str, str]]
    body: bytes


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        logger.info("Web %s", format % args)


class AdminWebServer:
    def __init__(self, host: str, port: int, app: "WebAdminApp") -> None:
        self.host = host
        self.port = port
        self.app = app
        self.httpd = make_server(host, port, app, handler_class=QuietRequestHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="web-admin")

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


class WebAdminApp:
    def __init__(self, settings: SettingsService, show_service: ShowService, poller: Poller, notifier_manager: NotifierManager) -> None:
        self.settings = settings
        self.show_service = show_service
        self.poller = poller
        self.notifier_manager = notifier_manager
        self.static_root = Path(__file__).with_name("static")
        self.sessions: dict[str, float] = {}
        self._session_lock = threading.RLock()

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        try:
            response = self._dispatch(environ)
        except Exception:
            logger.exception("Web 后台处理请求失败")
            response = self._render_message_page(
                title="服务器错误",
                message="后台处理请求时发生异常，请查看控制台日志。",
                status="500 Internal Server Error",
                level="error",
            )
        start_response(response.status, response.headers)
        return [response.body]

    def _dispatch(self, environ: dict[str, Any]) -> Response:
        request = self._build_request(environ)
        path = request["path"]
        method = request["method"]

        if path.startswith("/static/"):
            return self._serve_static(path)
        if path == "/favicon.ico":
            return self._empty_response()

        if path == "/login":
            if method == "GET":
                return self._render_login_page(request)
            if method == "POST":
                return self._handle_login(request)
            return self._method_not_allowed()

        if path == "/logout":
            if method != "POST":
                return self._method_not_allowed()
            return self._handle_logout(request)

        if not self._is_admin_password_configured():
            return self._render_message_page(
                title="后台未初始化",
                message="请先通过环境变量 WEB_ADMIN_PASSWORD 设置初始后台密码并重启服务。",
                status="503 Service Unavailable",
                level="warning",
            )

        if not self._is_authenticated(request):
            return self._redirect("/login")

        if path == "/":
            return self._render_dashboard(request)
        if path == "/settings":
            if method == "GET":
                return self._render_settings_page(request)
            if method == "POST":
                return self._handle_settings_save(request)
            return self._method_not_allowed()
        if path == "/settings/password":
            if method == "POST":
                return self._handle_admin_password_save(request)
            return self._method_not_allowed()
        if path == "/settings/test-notification" and method == "POST":
            return self._handle_test_notification()
        if path == "/shows":
            if method == "GET":
                return self._render_shows_page(request)
            if method == "POST":
                return self._handle_add_show(request)
            return self._method_not_allowed()
        if path == "/poll/check-all" and method == "POST":
            return self._handle_check_all()

        if path.startswith("/shows/") and method == "POST":
            return self._handle_show_action(request)

        return self._render_message_page(
            title="页面不存在",
            message="你访问的路径不存在。",
            status="404 Not Found",
            level="error",
        )

    def _build_request(self, environ: dict[str, Any]) -> dict[str, Any]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        query = {key: values[0] for key, values in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()}
        body = b""
        if method in {"POST", "PUT", "PATCH"}:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length) if length else b""
        form = {key: values[0] for key, values in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        raw_cookie = environ.get("HTTP_COOKIE", "")
        jar = cookies.SimpleCookie()
        if raw_cookie:
            jar.load(raw_cookie)
        return {
            "method": method,
            "path": path,
            "query": query,
            "form": form,
            "cookie": jar,
        }

    def _render_login_page(self, request: dict[str, Any]) -> Response:
        if not self._is_admin_password_configured():
            return self._render_message_page(
                title="后台未初始化",
                message="后台密码尚未初始化，请重启服务后重试。",
                status="503 Service Unavailable",
                level="warning",
            )
        body = f"""
        <section class='auth-shell'>
            <div class='auth-grid'>
                <section class='auth-stage'>
                    <p class='eyebrow'>Bangumi Notification Console</p>
                    <h1>简洁、统一地管理你的番剧通知。</h1>
                    <p class='subtle'>用一个安静、明亮、可长期使用的后台，把追番、通知器和轮询设置整理得井井有条。</p>
                    <ul class='auth-points'>
                        <li>查看最新缓存剧集与检查状态</li>
                        <li>统一配置 Telegram、Webhook 与轮询参数</li>
                        <li>快速添加、检查、停用和删除追番</li>
                    </ul>
                </section>
                <section class='auth-panel'>
                    <p class='auth-kicker'>管理员登录</p>
                    <h2>进入 Web 后台</h2>
                    <p class='subtle'>首次自动生成的后台密码只会在初始化时显示在控制台日志中。</p>
                    {self._flash_html(request)}
                    <form method='post' action='/login' class='stack-form'>
                        <input type='text' name='username' value='admin' autocomplete='username' class='visually-hidden' tabindex='-1' aria-hidden='true' />
                        <label>后台密码</label>
                        <input type='password' name='password' placeholder='请输入后台密码' autocomplete='current-password' required />
                        <button type='submit'>登录控制台</button>
                    </form>
                    <p class='auth-note'>若密码已遗失，可删除 <code>settings</code> 中的 <code>web.admin_password</code> 后重启，系统会重新生成一次性初始密码。</p>
                </section>
            </div>
        </section>
        """
        return self._html_response(self._page_layout("登录", body, request, show_nav=False))

    def _handle_login(self, request: dict[str, Any]) -> Response:
        password = request["form"].get("password", "")
        if password != (self.settings.get_admin_password() or ""):
            return self._render_login_page({**request, "query": {"level": "error", "message": "密码错误，请重试。"}})
        session_id = secrets.token_urlsafe(24)
        with self._session_lock:
            self.sessions[session_id] = time.time() + SESSION_TTL_SECONDS
        return self._redirect("/", cookies_to_set=[self._session_cookie(session_id)])

    def _handle_logout(self, request: dict[str, Any]) -> Response:
        session_id = request["cookie"].get(SESSION_COOKIE)
        if session_id:
            with self._session_lock:
                self.sessions.pop(session_id.value, None)
        expired = cookies.SimpleCookie()
        expired[SESSION_COOKIE] = ""
        expired[SESSION_COOKIE]["path"] = "/"
        expired[SESSION_COOKIE]["max-age"] = 0
        expired[SESSION_COOKIE]["httponly"] = True
        expired[SESSION_COOKIE]["samesite"] = "Lax"
        return self._redirect("/login", message="已退出后台。", level="success", cookies_to_set=[expired.output(header="").strip()])
    def _render_dashboard(self, request: dict[str, Any]) -> Response:
        stats = self.show_service.store.dashboard_stats()
        notifiers = self.notifier_manager.list_statuses()
        recent_episodes = self.show_service.store.recent_episodes(limit=6)
        shows = self.show_service.list_shows(include_inactive=True)[:6]
        latest_errors = [show for show in shows if show.get("last_error")]

        stat_cards = "".join(
            [
                self._stat_card("追踪中番剧", str(stats["active_shows"]), "Active"),
                self._stat_card("停用番剧", str(stats["disabled_shows"]), "Paused"),
                self._stat_card("已缓存剧集", str(stats["cached_episodes"]), "Episodes"),
                self._stat_card("启用通知器", str(sum(1 for item in notifiers if item["enabled"])), "Channels"),
            ]
        )

        notifier_items = "".join(
            f"<li><span>{escape(item['display_name'])}</span><strong class='status {'on' if item['enabled'] else 'off'}'>{'已开启' if item['enabled'] else '已关闭'}</strong></li>"
            for item in notifiers
        ) or "<li><span>暂无通知器</span></li>"

        recent_episode_items = "".join(
            f"<tr><td>{escape(item.get('show_title', '未知番剧'))}</td><td>{escape(item['episode_no'])}</td><td>{escape(item['title'])}</td><td>{escape(item.get('discovered_at') or '-')}</td></tr>"
            for item in recent_episodes
        ) or "<tr><td colspan='4'>暂无缓存剧集。</td></tr>"

        show_items = "".join(
            f"<tr><td>{escape(show['title'])}</td><td>{'追踪中' if show['status']=='active' else '已停用'}</td><td>{show['cached_episode_count']}</td><td>{escape(show.get('latest_episode_label') or '暂无')}</td></tr>"
            for show in shows
        ) or "<tr><td colspan='4'>暂无追番。</td></tr>"

        error_block = ""
        if latest_errors:
            error_items = "".join(
                f"<li><strong>{escape(show['title'])}</strong><span>{escape(show.get('last_error') or '')}</span></li>"
                for show in latest_errors[:4]
            )
            error_block = f"<section class='panel'><div class='panel-head'><h2>最近异常</h2></div><ul class='error-list'>{error_items}</ul></section>"

        body = f"""
        <section class='hero'>
            <div>
                <p class='eyebrow'>Control Deck</p>
                <h1>番剧通知控制台</h1>
                <p class='subtle'>集中查看最新追番状态、通知器开关与后台运行参数。</p>
            </div>
            <form method='post' action='/poll/check-all'>
                <button type='submit'>立即检查全部番剧</button>
            </form>
        </section>
        {self._flash_html(request)}
        <section class='stats-grid'>{stat_cards}</section>
        <section class='content-grid'>
            <section class='panel wide'>
                <div class='panel-head'><h2>最新缓存剧集</h2><a href='/shows'>前往追番管理</a></div>
                <table>
                    <thead><tr><th>番剧</th><th>集数</th><th>标题</th><th>记录时间</th></tr></thead>
                    <tbody>{recent_episode_items}</tbody>
                </table>
            </section>
            <section class='panel'>
                <div class='panel-head'><h2>通知器状态</h2><a href='/settings'>调整配置</a></div>
                <ul class='status-list'>{notifier_items}</ul>
            </section>
            <section class='panel wide'>
                <div class='panel-head'><h2>最近番剧</h2><a href='/shows'>查看全部</a></div>
                <table>
                    <thead><tr><th>标题</th><th>状态</th><th>缓存集数</th><th>最新一集</th></tr></thead>
                    <tbody>{show_items}</tbody>
                </table>
            </section>
            {error_block}
        </section>
        """
        return self._html_response(self._page_layout("仪表盘", body, request))

    def _render_settings_page(self, request: dict[str, Any]) -> Response:
        runtime = self.settings.describe_runtime()
        notifiers = {item["key"]: item for item in self.notifier_manager.list_statuses()}
        enabled_notifiers = [item["display_name"] for item in notifiers.values() if item["enabled"]]
        latest_cached = self.show_service.get_latest_cached_episode()
        if latest_cached is None:
            test_sample = "暂无缓存剧集，请先添加番剧。"
        else:
            show, episode = latest_cached
            test_sample = f"《{show['title']}》 / {episode['episode_no']} {episode['title']}"
        body = f"""
        <section class='hero compact'>
            <div>
                <p class='eyebrow'>Configuration Matrix</p>
                <h1>通知与系统配置</h1>
                <p class='subtle'>修改后尽量即时生效；监听地址变更将在保存后提示重启。</p>
            </div>
        </section>
        {self._flash_html(request)}
        <form method='post' action='/settings' class='settings-primary-form'>
            <div class='settings-grid'>
            <section class='panel'>
                <div class='panel-head'><h2>Telegram</h2></div>
                <label>Bot Token</label>
                <input type='password' name='telegram_bot_token' placeholder='留空则保持当前：{escape(runtime['telegram_bot_token_masked'])}' autocomplete='off' />
                <label>Chat ID</label>
                <input type='text' name='telegram_chat_id' value='{escape(runtime['telegram_chat_id'])}' placeholder='例如 123456789' autocomplete='off' inputmode='numeric' />
                <label class='checkbox'><input type='checkbox' name='enable_tg' {'checked' if notifiers.get('tg', {}).get('enabled') else ''} /> 启用 Telegram 通知</label>
            </section>
            <section class='panel'>
                <div class='panel-head'><h2>Dome Webhook</h2></div>
                <label>Webhook 地址</label>
                <input type='text' name='dome_webhook_url' placeholder='留空则保持当前：{escape(runtime['dome_webhook_masked'])}' autocomplete='url' />
                <label class='checkbox'><input type='checkbox' name='enable_dome' {'checked' if notifiers.get('dome', {}).get('enabled') else ''} /> 启用 Dome 通知</label>
                <label>B 站 Cookie</label>
                <textarea name='bilibili_cookie' rows='4' placeholder='留空则保持当前：{escape(runtime['bilibili_cookie_masked'])}' autocomplete='off'></textarea>
            </section>
            <section class='panel'>
                <div class='panel-head'><h2>调度与监听</h2></div>
                <label>轮询间隔（秒）</label>
                <input type='number' min='30' name='poll_interval_seconds' value='{runtime['poll_interval_seconds']}' autocomplete='off' />
                <label>Web Host</label>
                <input type='text' name='web_host' value='{escape(runtime['web_host'])}' autocomplete='url' />
                <label>Web Port</label>
                <input type='number' min='1' max='65535' name='web_port' value='{runtime['web_port']}' autocomplete='off' />
            </section>
            </div>
            <div class='actions-row settings-actions'>
                <button type='submit'>保存基础配置</button>
            </div>
        </form>
        <section class='settings-secondary-grid'>
            <form method='post' action='/settings/password' class='panel settings-card-form'>
                <div class='panel-head'><h2>后台安全</h2></div>
                <input type='text' name='username' value='admin' autocomplete='username' class='visually-hidden' tabindex='-1' aria-hidden='true' />
                <label>新后台密码</label>
                <input type='password' name='web_admin_password' placeholder='留空则保持当前：{escape(runtime['admin_password_masked'])}' autocomplete='new-password' />
                <label>确认新后台密码</label>
                <input type='password' name='web_admin_password_confirm' placeholder='再次输入新密码' autocomplete='new-password' />
                <p class='form-help'>当前状态：{'已配置后台密码' if runtime['admin_password_configured'] else '未配置后台密码'}</p>
                <div class='card-actions'>
                    <button type='submit' class='ghost'>更新后台密码</button>
                </div>
            </form>
            <form method='post' action='/settings/test-notification' class='panel settings-card-form'>
                <div class='panel-head'><h2>测试通知</h2></div>
                <p class='form-help'>将使用最近缓存的一集，按真实更新格式向当前已启用通道发送一次测试消息。</p>
                <p class='form-help'>测试样本：{escape(test_sample)}</p>
                <p class='form-help'>目标通道：{escape('、'.join(enabled_notifiers) if enabled_notifiers else '暂无已启用通道')}</p>
                <div class='card-actions'>
                    <button type='submit' class='ghost'>发送最新一集测试通知</button>
                </div>
            </form>
        </section>
        """
        return self._html_response(self._page_layout("设置", body, request))

    def _handle_settings_save(self, request: dict[str, Any]) -> Response:
        form = request["form"]
        password = form.get("web_admin_password", "").strip()
        confirm = form.get("web_admin_password_confirm", "").strip()
        if password and password != confirm:
            return self._redirect("/settings", message="两次输入的后台密码不一致。", level="error")

        for key in ("poll_interval_seconds", "web_port"):
            raw = form.get(key, "").strip()
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    return self._redirect("/settings", message=f"{key} 必须是整数。", level="error")
                if key == "poll_interval_seconds" and value < 30:
                    return self._redirect("/settings", message="轮询间隔不能小于 30 秒。", level="error")
                if key == "web_port" and not (1 <= value <= 65535):
                    return self._redirect("/settings", message="Web 端口必须在 1 到 65535 之间。", level="error")

        change_result = self.settings.save_web_settings(form)
        for notifier in self.notifier_manager.list_statuses():
            enabled = request["form"].get(f"enable_{notifier['key']}") == "on"
            self.notifier_manager.set_enabled(notifier["key"], enabled)

        message = "配置已保存并即时应用。"
        if change_result.restart_required:
            message += " Web host/port 已更新，需重启程序后生效。"
        return self._redirect("/settings", message=message, level="success")

    def _handle_admin_password_save(self, request: dict[str, Any]) -> Response:
        form = request["form"]
        password = form.get("web_admin_password", "").strip()
        confirm = form.get("web_admin_password_confirm", "").strip()
        if not password:
            return self._redirect("/settings", message="请输入新的后台密码。", level="warning")
        if password != confirm:
            return self._redirect("/settings", message="两次输入的后台密码不一致。", level="error")

        self.settings.save_web_settings({"web_admin_password": password})
        return self._redirect("/settings", message="后台密码已更新。", level="success")

    def _handle_test_notification(self) -> Response:
        try:
            summary = self.show_service.send_test_notification(self.notifier_manager)
        except Exception as exc:
            return self._redirect("/settings", message=f"测试通知发送失败：{exc}", level="error")

        level = "success" if summary.success_count == summary.attempted_count else "warning"
        return self._redirect("/settings", message=summary.short_message(), level=level)

    def _render_shows_page(self, request: dict[str, Any]) -> Response:
        shows = self.show_service.list_shows(include_inactive=True)
        rows = []
        for show in shows:
            toggle_label = "停用" if show["status"] == "active" else "启用"
            rows.append(
                f"""
                <tr>
                    <td><strong>{escape(show['title'])}</strong><span class='table-sub'>{escape(show['source_url'])}</span></td>
                    <td>{'追踪中' if show['status'] == 'active' else '已停用'}</td>
                    <td>{show['cached_episode_count']}</td>
                    <td>{escape(show.get('latest_episode_label') or '暂无')}</td>
                    <td>{escape(show.get('last_checked_at') or '-')}</td>
                    <td>{escape(show.get('last_error') or '-')}</td>
                    <td>
                        <div class='row-actions'>
                            <form method='post' action='/shows/{show['id']}/check'><button type='submit'>检查</button></form>
                            <form method='post' action='/shows/{show['id']}/toggle'><button type='submit'>{toggle_label}</button></form>
                            <form method='post' action='/shows/{show['id']}/delete'><button type='submit' class='danger'>删除</button></form>
                        </div>
                    </td>
                </tr>
                """
            )
        body = f"""
        <section class='hero compact'>
            <div>
                <p class='eyebrow'>Show Registry</p>
                <h1>追番管理</h1>
                <p class='subtle'>添加、查看和维护已追踪的番剧，支持单部检查与启停。</p>
            </div>
            <form method='post' action='/poll/check-all'>
                <button type='submit'>检查全部</button>
            </form>
        </section>
        {self._flash_html(request)}
        <section class='shows-stack'>
            <section class='panel add-show-panel'>
                <div class='panel-head'><h2>添加番剧</h2></div>
                <form method='post' action='/shows' class='inline-form'>
                    <input type='text' name='show_input' placeholder='输入 B 站番剧链接、ss127870 或 ep2612898' required />
                    <button type='submit'>添加并缓存</button>
                </form>
            </section>
            <section class='panel'>
                <div class='panel-head'><h2>当前追番列表</h2></div>
                <table>
                    <thead><tr><th>番剧</th><th>状态</th><th>缓存集数</th><th>最新一集</th><th>最近检查</th><th>最近错误</th><th>操作</th></tr></thead>
                    <tbody>{''.join(rows) or "<tr><td colspan='7'>暂无追番记录。</td></tr>"}</tbody>
                </table>
            </section>
        </section>
        """
        return self._html_response(self._page_layout("追番管理", body, request))

    def _handle_add_show(self, request: dict[str, Any]) -> Response:
        raw = request["form"].get("show_input", "").strip()
        if not raw:
            return self._redirect("/shows", message="请输入番剧链接或 ss/ep 编号。", level="error")
        try:
            result = self.show_service.add_show(raw)
        except Exception as exc:
            return self._redirect("/shows", message=f"添加失败：{exc}", level="error")
        message = f"《{result['show']['title']}》已{'加入追踪' if result['created'] else '重新启用/已存在'}，当前缓存 {result['cached_episode_count']} 集。"
        return self._redirect("/shows", message=message, level="success")

    def _handle_check_all(self) -> Response:
        summary = self.poller.check_all()
        return self._redirect("/", message=summary.short_message(), level="success" if not summary.errors else "warning")

    def _handle_show_action(self, request: dict[str, Any]) -> Response:
        parts = [segment for segment in request["path"].split("/") if segment]
        if len(parts) != 3:
            return self._render_message_page("操作无效", "请求路径不正确。", "404 Not Found", "error")
        _, show_id_raw, action = parts
        try:
            show_id = int(show_id_raw)
        except ValueError:
            return self._render_message_page("操作无效", "番剧 ID 不合法。", "400 Bad Request", "error")

        show = self.show_service.store.get_show_by_id(show_id)
        if show is None:
            return self._redirect("/shows", message="番剧不存在。", level="error")

        if action == "check":
            summary = self.poller.check_show(show_id)
            return self._redirect("/shows", message=summary.short_message(), level="success" if not summary.errors else "warning")
        if action == "toggle":
            updated = self.show_service.set_show_status(show_id, enabled=show["status"] != "active")
            return self._redirect("/shows", message=f"《{updated['title']}》已切换为{'追踪中' if updated['status'] == 'active' else '已停用'}。", level="success")
        if action == "delete":
            self.show_service.delete_show(show_id)
            return self._redirect("/shows", message=f"《{show['title']}》已删除。", level="success")
        return self._redirect("/shows", message="未知操作。", level="error")

    def _page_layout(self, title: str, body: str, request: dict[str, Any], show_nav: bool = True) -> str:
        nav = ""
        if show_nav:
            nav = """
            <nav class='shell-nav'>
                <div class='nav-brand'>Bangumi Console</div>
                <div class='nav-links'>
                    <a href='/'>仪表盘</a>
                    <a href='/shows'>追番管理</a>
                    <a href='/settings'>通知与设置</a>
                </div>
                <form method='post' action='/logout'><button type='submit' class='ghost'>退出</button></form>
            </nav>
            """
        return f"""
        <!doctype html>
        <html lang='zh-CN'>
        <head>
            <meta charset='utf-8' />
            <meta name='viewport' content='width=device-width, initial-scale=1' />
            <title>{escape(title)} · 番剧通知后台</title>
            <link rel='stylesheet' href='/static/admin.css' />
        </head>
        <body>
            <main class='shell'>
                {nav}
                {body}
            </main>
        </body>
        </html>
        """

    def _serve_static(self, path: str) -> Response:
        relative = path.removeprefix('/static/').strip()
        target = (self.static_root / relative).resolve()
        if self.static_root.resolve() not in target.parents and target != self.static_root.resolve():
            return self._render_message_page("资源不存在", "静态资源路径无效。", "404 Not Found", "error")
        if not target.exists() or not target.is_file():
            return self._render_message_page("资源不存在", "找不到请求的静态资源。", "404 Not Found", "error")
        content_type = 'text/css; charset=utf-8' if target.suffix == '.css' else 'application/octet-stream'
        body = target.read_bytes()
        return Response("200 OK", [("Content-Type", content_type), ("Content-Length", str(len(body)))], body)

    def _flash_html(self, request: dict[str, Any]) -> str:
        query = request.get("query", {})
        message = query.get("message", "")
        level = query.get("level", "success")
        if not message:
            return ""
        return f"<div class='flash {escape(level)}'>{escape(message)}</div>"

    def _stat_card(self, title: str, value: str, label: str) -> str:
        return f"<article class='stat-card'><p>{escape(label)}</p><strong>{escape(value)}</strong><span>{escape(title)}</span></article>"

    def _is_admin_password_configured(self) -> bool:
        return bool(self.settings.get_admin_password())

    def _is_authenticated(self, request: dict[str, Any]) -> bool:
        session = request["cookie"].get(SESSION_COOKIE)
        if session is None:
            return False
        with self._session_lock:
            self._cleanup_sessions()
            expires_at = self.sessions.get(session.value)
            return bool(expires_at and expires_at > time.time())

    def _cleanup_sessions(self) -> None:
        now = time.time()
        expired = [key for key, expires_at in self.sessions.items() if expires_at <= now]
        for key in expired:
            self.sessions.pop(key, None)

    def _session_cookie(self, session_id: str) -> str:
        jar = cookies.SimpleCookie()
        jar[SESSION_COOKIE] = session_id
        jar[SESSION_COOKIE]["path"] = "/"
        jar[SESSION_COOKIE]["httponly"] = True
        jar[SESSION_COOKIE]["samesite"] = "Lax"
        jar[SESSION_COOKIE]["max-age"] = SESSION_TTL_SECONDS
        return jar.output(header="").strip()

    def _redirect(self, path: str, message: str | None = None, level: str = "success", cookies_to_set: list[str] | None = None) -> Response:
        if message:
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}{urlencode({'message': message, 'level': level})}"
        headers = [("Location", path)]
        if cookies_to_set:
            for cookie in cookies_to_set:
                headers.append(("Set-Cookie", cookie))
        return Response("303 See Other", headers, b"")

    def _html_response(self, html: str, status: str = "200 OK", headers: list[tuple[str, str]] | None = None) -> Response:
        encoded = html.encode("utf-8")
        response_headers = [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(encoded)))]
        if headers:
            response_headers.extend(headers)
        return Response(status, response_headers, encoded)

    def _empty_response(self, status: str = "204 No Content") -> Response:
        return Response(status, [("Content-Length", "0")], b"")

    def _render_message_page(self, title: str, message: str, status: str, level: str) -> Response:
        body = f"<div class='flash {escape(level)}'>{escape(message)}</div><section class='hero compact'><div><p class='eyebrow'>Bangumi Control Room</p><h1>{escape(title)}</h1><p class='subtle'>{escape(message)}</p></div></section>"
        return self._html_response(self._page_layout(title, body, {'query': {}}, show_nav=False), status=status)

    def _method_not_allowed(self) -> Response:
        return self._render_message_page("方法不允许", "当前路径不支持这个请求方法。", "405 Method Not Allowed", "error")
