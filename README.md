# 哔哩哔哩番剧通知

一个轻量的 Python 小工具，用来追踪 B 站番剧更新、缓存已知剧集，并通过 Telegram、自定义 webhook 与 Web 控制后台统一管理通知。

## 当前能力

- 追踪 `ss` / `ep` / 完整番剧播放链接
- 首次添加时缓存当前所有剧集，不补发历史通知
- 定时轮询 B 站番剧接口，发现新剧集后自动通知
- 支持基于“最近缓存的一集”发送测试通知，便于验证 Telegram / 自定义 webhook 通道
- 内置 `Telegram` Bot 菜单：`/start`、`/menu`
- 内置 `api/dome.py` 示例通知器，可作为自定义 webhook 模板
- 提供默认监听 `0.0.0.0:8688` 的 Web 控制后台
- Web 后台可查看最新番剧、追番列表、通知器状态与运行配置
- 未设置后台密码时，首次启动会自动生成随机密码，并仅在本次控制台日志中显示一次

## 环境要求

- Python `3.10+`
- Windows `PowerShell` 或 `CMD`
- 可选：Telegram Bot Token、Telegram Chat ID、自定义 Webhook

## 使用 `.env` 配置

程序启动时会自动读取项目根目录的 `.env` 文件；如果系统环境变量里存在同名配置，则系统环境变量优先。

先复制模板：

```powershell
Copy-Item .env.example .env
```

```cmd
copy .env.example .env
```

`.env` 支持以下配置项：

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DOME_WEBHOOK_URL=
WEB_ADMIN_PASSWORD=
WEB_HOST=127.0.0.1
WEB_PORT=8688
DB_PATH=data/app.db
POLL_INTERVAL_SECONDS=1800
HTTP_TIMEOUT_SECONDS=20
TELEGRAM_POLL_TIMEOUT_SECONDS=2
BILIBILI_COOKIE=
```

说明：

- `WEB_HOST` 建议本机使用时设为 `127.0.0.1`，避免直接暴露到局域网
- `WEB_ADMIN_PASSWORD` 留空时，首次启动会自动生成一次性初始密码
- `BILIBILI_COOKIE` 仅在访问受限番剧时需要

## 快速开始

### 1. 克隆仓库

```powershell
git clone https://github.com/isxlan0/bilibili-anime-monitor.git
cd bilibili-anime-monitor
```

```cmd
git clone https://github.com/isxlan0/bilibili-anime-monitor.git
cd bilibili-anime-monitor
```

### 2. 创建虚拟环境并安装依赖

当前项目仅使用 Python 标准库，`requirements.txt` 主要用于统一安装入口。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

```cmd
py -3.10 -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 配置 `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

```cmd
copy .env.example .env
notepad .env
```

如果你准备先用本机浏览器管理，建议把 `.env` 里的 `WEB_HOST` 设为 `127.0.0.1`。

### 4. 启动程序

```powershell
python main.py
```

```cmd
python main.py
```

如果未显式设置 `WEB_ADMIN_PASSWORD`，程序会在第一次初始化后台时自动生成随机密码，并在控制台打印一次：

- Web 后台：`http://127.0.0.1:8688/`
- Telegram：先给 Bot 发送 `/start` 绑定聊天，再发送 `/menu`

## Web 后台能力

- 仪表盘：查看最新缓存剧集、通知器状态、最近异常
- 追番管理：添加番剧、单部检查、全量检查、启用/停用、删除
- 通知与设置：修改 TG Token、Chat ID、Webhook、B 站 Cookie、轮询间隔、后台密码
- 测试通知：使用最近缓存的一集向已启用通道发送一次测试更新
- 热更新：通知参数、轮询间隔保存后立即生效
- 重启生效：`WEB_HOST`、`WEB_PORT` 保存后会提示重启程序
- UI 风格：白色为主、简洁、大气的极简后台界面

## 丢失后台密码怎么办

如果忘记了首次随机密码，可删除 SQLite `settings` 表中的 `web.admin_password`，然后重启程序。程序会重新生成新的随机后台密码，并再次只在该次启动日志中显示一次。

## 自定义通知器

`api/dome.py` 是一个最小可用模板。后续新增渠道时，可参考它实现：

- `key`
- `display_name`
- `send_text()`
- `send_episode_update()`

然后在 `main.py` 中注册即可。

## 测试

```powershell
python -m unittest discover -s tests -v
```

```cmd
python -m unittest discover -s tests -v
```