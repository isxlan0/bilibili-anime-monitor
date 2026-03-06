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
- 如不需要 Web 后台 则只需配置 Tg Token 即可在 Tg 中管理

## 环境要求

- Python `3.9+`
- Windows：`PowerShell` 或 `CMD`
- Linux：`bash` / `sh`
- 可选：Telegram Bot Token、Telegram Chat ID、自定义 Webhook

## 使用 `.env` 配置

程序启动时会自动读取项目根目录的 `.env` 文件；如果系统环境变量里存在同名配置，则系统环境变量优先。

先复制模板：

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

### Windows CMD

```cmd
copy .env.example .env
```

### Linux

```bash
cp .env.example .env
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

各配置项说明：

- `TELEGRAM_BOT_TOKEN`：Telegram 机器人 Token，用于发送通知和接收 Bot 菜单指令
- `TELEGRAM_CHAT_ID`：Telegram 聊天 ID，用于指定默认接收通知的聊天窗口或频道
- `DOME_WEBHOOK_URL`：自定义 Webhook 地址；启用对应通知器后，更新会推送到这个地址
- `WEB_ADMIN_PASSWORD`：Web 后台管理员密码；留空时，首次启动会自动生成一次性初始密码
- `WEB_HOST`：Web 后台监听地址；本机使用建议设为 `127.0.0.1`，避免直接暴露到局域网
- `WEB_PORT`：Web 后台监听端口，默认 `8688`
- `DB_PATH`：SQLite 数据库文件路径，用于保存番剧、剧集、通知器状态和系统设置
- `POLL_INTERVAL_SECONDS`：轮询 B 站番剧更新的时间间隔，单位为秒
- `HTTP_TIMEOUT_SECONDS`：程序请求 B 站、Telegram 或 Webhook 时的 HTTP 超时时间，单位为秒
- `TELEGRAM_POLL_TIMEOUT_SECONDS`：Telegram Bot 拉取更新的超时时间，单位为秒
- `BILIBILI_COOKIE`：B 站 Cookie；当部分番剧接口需要登录态或访问受限时使用

## 快速开始

### 1. 克隆仓库

#### Windows PowerShell

```powershell
git clone https://github.com/isxlan0/bilibili-anime-monitor.git
cd bilibili-anime-monitor
```

#### Windows CMD

```cmd
git clone https://github.com/isxlan0/bilibili-anime-monitor.git
cd bilibili-anime-monitor
```

#### Linux

```bash
git clone https://github.com/isxlan0/bilibili-anime-monitor.git
cd bilibili-anime-monitor
```

### 2. 创建虚拟环境并安装依赖

当前项目仅使用 Python 标准库，`requirements.txt` 主要用于统一安装入口。

#### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### Windows CMD

```cmd
py -3 -m venv .venv
.\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

#### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 配置 `.env`

#### Windows PowerShell

```powershell
Copy-Item .env.example .env
notepad .env
```

#### Windows CMD

```cmd
copy .env.example .env
notepad .env
```

#### Linux

```bash
cp .env.example .env
nano .env
```

如果你准备先用本机浏览器管理，建议把 `.env` 里的 `WEB_HOST` 设为 `127.0.0.1`。

### 4. 启动程序

#### Windows PowerShell

```powershell
python main.py
```

#### Windows CMD

```cmd
python main.py
```

#### Linux

```bash
python3 main.py
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

### Windows PowerShell

```powershell
python -m unittest discover -s tests -v
```

### Windows CMD

```cmd
python -m unittest discover -s tests -v
```

### Linux

```bash
python3 -m unittest discover -s tests -v
```
