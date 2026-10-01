# Auto YunShangHeKai

一个使用 Python 3.11+ 编写的“云上河开”后台学习管理器。项目复现网站正常前端的课程发现、状态查询、heartbeat 与完成确认流程，实现更直观、更快速的课程管理与进度推进。


> [!IMPORTANT]
> 本项目不涉及对**云上河开**平台的”破解“或”“逆向”，无法实现“快速刷课“、”一键完成课程任务“，并不篡改或伪造学习进度。
> 
> 本项目并非**云上河开**平台官方产品，请仅在本人账号、获授权环境以及学校和平台规则允许的范围内使用。
## 功能

- **云上河开**登录，账号信息与课程列表获取。
- 为 activity 编号，显示服务器进度和完成状态。
- 按网站正常节奏发送 heartbeat，定期查询服务器认可的学习进度。
- 自动连播、任务切换、已完成跳过、断点续跑和网络重试。
- 自动检测平台限制多活跃任务，切换安全串行模式。

## 技术栈

| 组件 | 用途 |
| --- | --- |
| Python 3.11+ | 运行环境 |
| asyncio | 异步调度与 heartbeat 循环 |
| httpx | HTTP 会话、Cookie 和 API 请求 |
| pydantic | 配置与服务器数据校验 |
| Typer | 命令行接口 |
| Rich | 菜单、表格、颜色和进度条 |
| Tenacity | 网络请求重试 |
| Playwright | 首次交互式登录与 session 提取 |

## 快速开始

### Windows

安装 [Python 3.11+](https://www.python.org/downloads/) 后，双击 `start.cmd`，或在 PowerShell 中运行：

```powershell
.\start.cmd
```

脚本会在首次启动时自动创建 `.venv` 并安装依赖。

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

### 手动安装

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[browser,dev]"
python -m cloudriver_manager
```

如果系统没有可供 Playwright 驱动的 Chrome 或 Edge 内核浏览器，再执行：

```bash
python -m playwright install chromium
```

## 首次登录

首次启动且未检测到 `.cloudriver/session.json` 时，程序会主动显示登录说明并询问是否立即登录。

1. 程序优先检测并使用系统默认的 Chrome 或 Edge 内核。
2. 登录在独立临时浏览器配置中进行，不复用或锁定日常浏览器用户目录。
3. 账号和密码只在平台官方页面输入，程序不会保存明文密码。
4. 平台返回 `ONLINE` 后自动保存 session，并立即显示当前账户信息。

`session.json` 相当于临时登录凭据，请勿分享或提交到 Git。详见 [SECURITY.md](SECURITY.md)。

## 菜单

```text
1. 课程状态
2. 开始学习
3. 登录/刷新
4. 登录状态
5. 功能说明
6. 初始化配置
7. 检测更新
0. 退出程序
```

选择“指定序号”开始学习时，程序会先自动显示一次课程状态列表，再询问起始序号。选择“直接开始”则从服务器返回的第一个未完成任务开始。

## 命令行

```bash
cloudriver init
cloudriver login
cloudriver login-status
cloudriver status
cloudriver run
cloudriver run --start 12
cloudriver check-update
```

如已在浏览器获得尚未消费的 CAS 回跳 URL，也可使用：

```bash
cloudriver login --ticket-url "https://jxapp.open.ha.cn/?ticket=..."
```

## 配置与数据

默认数据目录为 `.cloudriver/`：

| 文件 | 内容 |
| --- | --- |
| `config.json` | heartbeat 间隔、超时、并发策略等配置 |
| `session.json` | 登录 Cookie 与前端会话信息，禁止公开 |
| `checkpoint.json` | 调度断点；不会覆盖服务器状态 |
| `manager.log` | 运行日志 |

平台当前会拒绝多 activity 同时学习，因此默认配置为 `max_workers=1`、`probe_parallelism=false`。

## 手动检测更新

菜单中的“检测更新”只会在用户手动触发时运行。程序会从 Git `origin` 获取远程状态，显示待更新提交，并且只有在用户明确同意后才会：

1. 执行 fast-forward-only 源代码更新。
2. 重新执行本地可编辑安装并更新依赖。
3. 自动重启程序。

如果检测到未提交的已跟踪文件修改、分支分叉或安装失败，程序会停止更新或回滚。此功能需要通过 `git clone` 获取的工作副本；直接下载的 ZIP 不包含 Git 远程信息，无法自动更新。

## 工作原理

程序通过课程目录接口发现任务，通过 activity `display` 接口获取服务器进度。资源学习过程中按照正常前端间隔发送 heartbeat；达到服务器返回的资源长度且不存在关联作业后，调用完成接口并再次查询 `resourceFinished`。本地 checkpoint 只用于恢复调度，绝不会被当作“已经完成”的证据。

作业、考试、论坛发帖等需要实际作答或提交的交互任务不会被伪造为完成。

## 开发与测试

```bash
python -m pip install -e ".[browser,dev]"
python -m pytest -q
```

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。提交公开 issue 或代码前务必移除 Cookie、ticket、真实姓名、学号、用户 ID、课程信息和 HAR 抓包。

## 致谢

- 感谢项目 [Rain48698/YunShangHeKai](https://github.com/Rain48698/YunShangHeKai) 带来的思路与启发。
- 感谢 [ChatGPT](https://chatgpt.com/) 在协议整理、架构设计、代码实现、测试和文档完善过程中提供协助。

## 许可证

本项目使用 [MIT License](LICENSE) 开源。

## 免责声明

本项目仅用于技术学习、协议研究和个人学习流程管理。使用者应遵守法律法规、学校规定和平台用户协议，并自行承担使用风险。项目作者及贡献者不对账号限制、学习记录异常或其他损失承担责任。
