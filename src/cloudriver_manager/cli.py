from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from .auth import browser_login, ticket_login
from .client import PlatformClient
from .config import Settings
from .engine import LearningEngine
from .errors import CloudRiverError, SessionExpired
from .har import analyze_har
from .state import CheckpointStore
from .updater import GitUpdater, UpdateError

app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="云上河开后台学习管理器",
)
console = Console()
ConfigOption = Annotated[Path | None, typer.Option("--config", "-c", help="配置 JSON 路径")]


def settings_for(path: Path | None) -> Settings:
    return Settings.load(path)


def completion_cell(finished: bool) -> Text:
    """统一渲染完成状态，避免终端主题导致是/否难以分辨。"""
    return Text("是", style="bold green") if finished else Text("否", style="bold red")


async def fetch_current_account(settings: Settings) -> dict[str, str]:
    """确认会话在线并返回当前账户信息。"""
    async with PlatformClient(settings) as client:
        if not await client.check_online():
            raise SessionExpired("当前会话不是 ONLINE。")
        info = client.account_info()
        # 早期 session 可能未保存身份字段；尝试通过 CAS 重新取回一次。
        if not info and await client.silent_cas_login():
            info = client.account_info()
        return info


def render_account_info(info: dict[str, str]) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("登录状态", Text("ONLINE", style="bold green"))
    table.add_row("姓名", info.get("realName", "未返回"))
    table.add_row("学号/用户名", info.get("userName", "未返回"))
    table.add_row("用户 ID", info.get("userId", "未返回"))
    table.add_row("账户角色", info.get("role", "未返回"))
    console.print(Panel.fit(table, title="当前账户", border_style="green"))


_SERVER_PROGRESS_RE = re.compile(
    r"^(?P<name>.+): 服务器 (?P<current>\d+(?:\.\d+)?)/(?P<total>\d+(?:\.\d+)?) 秒$"
)


def parse_server_progress(message: str) -> tuple[str, float, float] | None:
    """从引擎的进度通知中提取 activity 名称与服务器时长。"""
    match = _SERVER_PROGRESS_RE.match(message)
    if not match:
        return None
    return match["name"], float(match["current"]), float(match["total"])


class LearningProgress:
    """将学习进度通知渲染成原地刷新的 Rich 进度条。"""

    def __init__(self, output: Console) -> None:
        self.progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[cyan]{task.description}[/cyan]"),
            BarColumn(bar_width=32, complete_style="green", finished_style="bold green"),
            TextColumn("[bold]{task.percentage:>5.1f}%[/bold]"),
            TextColumn(
                "[blue]{task.fields[server_current]:.0f}/"
                "{task.fields[server_total]:.0f} 秒[/blue]"
            ),
            console=output,
            refresh_per_second=8,
            transient=False,
        )
        self.task_id: TaskID | None = None
        self.started = False
        self.description = "正在读取服务器进度"

    def _start(self) -> None:
        if not self.started:
            self.progress.start()
            self.started = True

    def _ensure_task(self) -> TaskID:
        self._start()
        if self.task_id is None:
            self.task_id = self.progress.add_task(
                self.description,
                total=1,
                completed=0,
                server_current=0.0,
                server_total=0.0,
            )
        return self.task_id

    def notify(self, level: str, message: str) -> None:
        if level == "start":
            self.description = message
            task_id = self._ensure_task()
            self.progress.reset(
                task_id,
                description=message,
                total=1,
                completed=0,
                server_current=0.0,
                server_total=0.0,
            )
            return

        if level == "progress":
            parsed = parse_server_progress(message)
            if parsed is not None:
                _name, current, total = parsed
                task_id = self._ensure_task()
                safe_total = max(total, 1.0)
                self.progress.update(
                    task_id,
                    completed=min(current, safe_total),
                    total=safe_total,
                    server_current=current,
                    server_total=total,
                    refresh=True,
                )
                return

        styles = {"warning": "yellow", "done": "green", "info": "white"}
        if level == "done" and self.task_id is not None:
            task = self.progress.tasks[self.task_id]
            server_total = float(task.fields.get("server_total", task.total))
            self.progress.update(
                self.task_id,
                completed=task.total,
                server_current=server_total,
                refresh=True,
            )
        self.progress.console.print(f"[{styles.get(level, 'white')}]{message}[/]")

    def close(self) -> None:
        if self.started:
            self.progress.stop()
            self.started = False


@app.command()
def init(config: ConfigOption = None) -> None:
    """生成默认配置。"""
    settings = settings_for(config)
    destination = settings.save(config)
    console.print(f"[green]已写入[/green] {destination}")


@app.command()
def login(
    config: ConfigOption = None,
    ticket_url: Annotated[str | None, typer.Option(help="可选：刚复制的 CAS 回跳 URL")] = None,
) -> None:
    """首次登录并保存会话；账号密码只在平台页面中输入。"""
    settings = settings_for(config)
    try:
        path = (
            asyncio.run(ticket_login(settings, ticket_url))
            if ticket_url
            else asyncio.run(browser_login(settings))
        )
    except CloudRiverError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]会话已保存[/green] {path}")
    try:
        render_account_info(asyncio.run(fetch_current_account(settings)))
    except CloudRiverError as exc:
        # 会话已保存，身份回显失败不应将本次登录标记为失败。
        console.print(f"[yellow]登录成功，但读取账户信息失败：{exc}[/yellow]")


@app.command("login-status")
def login_status(config: ConfigOption = None) -> None:
    """确认当前会话是否在线，并显示服务器下发的账户身份。"""
    settings = settings_for(config)

    try:
        render_account_info(asyncio.run(fetch_current_account(settings)))
    except CloudRiverError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def status(config: ConfigOption = None) -> None:
    """从服务器查询课程、activity 和完成状态。"""
    settings = settings_for(config)

    async def _run() -> None:
        async with PlatformClient(settings) as client:
            if not await client.check_online():
                raise SessionExpired("当前会话不是 ONLINE。")
            table = Table("序号", "课程", "Activity", "类型", "服务器进度", "完成")
            ordinal = 0
            for course in await client.list_courses():
                for unit in await client.list_units(course.course_version_id):
                    for activity in unit.activitys:
                        ordinal += 1
                        if activity.is_resource_activity:
                            remote = await client.activity_status(
                                course.course_version_id, activity.activity_id
                            )
                            progress = (
                                f"{remote.progress_seconds:.0f}/{remote.required_seconds:.0f}s"
                            )
                            finished = completion_cell(remote.server_finished)
                        else:
                            progress = "交互任务（服务器目录状态）"
                            finished = completion_cell(
                                course.finished or activity.is_catalog_finished
                            )
                        table.add_row(
                            str(ordinal),
                            course.course_name,
                            activity.activity_name,
                            str(activity.type),
                            progress,
                            finished,
                        )
            console.print(table)

    try:
        asyncio.run(_run())
    except CloudRiverError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def run(
    config: ConfigOption = None,
    once: Annotated[bool, typer.Option(help="只执行一次发现/调度，不循环重新发现")] = False,
    start: Annotated[
        int, typer.Option("--start", "-s", min=1, help="从状态列表的指定序号开始")
    ] = 1,
) -> None:
    """按正常前端节奏维护 heartbeat，以服务器状态驱动任务切换。"""
    settings = settings_for(config)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RichHandler(console=console),
            logging.FileHandler(settings.log_file, encoding="utf-8"),
        ],
    )
    # httpx 的每条 URL 日志会淹没真正的学习进度，详细请求仍保留在需要时的调试路径中。
    logging.getLogger("httpx").setLevel(logging.WARNING)

    progress_ui = LearningProgress(console)

    async def _run() -> None:
        renewed = False
        while True:
            try:
                async with PlatformClient(settings) as client:
                    if not await client.check_online():
                        raise SessionExpired("当前会话不是 ONLINE。")
                    engine = LearningEngine(
                        client,
                        settings,
                        CheckpointStore(settings.checkpoint_file),
                        notify=progress_ui.notify,
                    )
                    await engine.run(once=once, start_index=start)
                    return
            except SessionExpired:
                if renewed:
                    raise
                progress_ui.notify(
                    "warning", "session 已失效，将打开登录页刷新会话；服务器进度不会丢失。"
                )
                await browser_login(settings)
                try:
                    render_account_info(await fetch_current_account(settings))
                except CloudRiverError as exc:
                    progress_ui.notify(
                        "warning", f"登录成功，但读取账户信息失败：{exc}"
                    )
                renewed = True

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        progress_ui.close()
        console.print("[yellow]已停止；下次会从服务器状态续跑。[/yellow]")
    except CloudRiverError as exc:
        progress_ui.close()
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    finally:
        progress_ui.close()


@app.command("inspect-har")
def inspect_har(paths: list[Path]) -> None:
    """离线检查 HAR；不会输出 cookie、ticket 或 heartbeat token。"""
    for path in paths:
        report = analyze_har(path)
        console.print_json(json.dumps(report, ensure_ascii=False))


def restart_application(config: Path | None = None) -> None:
    """用当前 Python 解释器重启菜单，使新版代码立即生效。"""
    args = [sys.executable, "-m", "cloudriver_manager"]
    if config is not None:
        args.extend(["--config", str(config)])
    console.print("[green]更新完成，正在重启程序…[/green]")
    os.execv(sys.executable, args)


@app.command("check-update")
def check_update(
    config: ConfigOption = None,
    restart: Annotated[
        bool, typer.Option(help="更新完成后自动重启程序")
    ] = True,
) -> None:
    """手动检测 Git origin，经确认后快进更新并覆盖安装。"""
    try:
        with console.status("正在获取远程版本…", spinner="dots"):
            updater = GitUpdater()
            info = updater.check()
    except UpdateError as exc:
        console.print(f"[red]检测更新失败：{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"远程仓库：{info.remote_url}")
    if not info.available:
        if info.local_ahead:
            console.print("[yellow]本地版本领先于远程，无需更新。[/yellow]")
        else:
            console.print("[green]当前已是最新版本。[/green]")
        return

    console.print(
        f"[yellow]发现新版本[/yellow] "
        f"{info.current_commit[:8]} → {info.latest_commit[:8]}"
    )
    if info.changes:
        console.print("[bold]更新内容：[/bold]")
        for change in info.changes:
            console.print(f"  • {change}")
    if not Confirm.ask("是否同意拉取并覆盖安装新版本", default=False):
        console.print("已取消更新。")
        return

    try:
        with console.status("正在更新源代码并安装依赖…", spinner="dots"):
            updater.apply(info)
    except UpdateError as exc:
        console.print(f"[red]更新失败：{exc}[/red]")
        raise typer.Exit(1) from exc

    if restart:
        restart_application(config)
    else:
        console.print("[green]更新完成，重新启动后生效。[/green]")


def show_menu() -> None:
    console.print(
        Panel.fit(
            "[bold bright_green]1. 课程状态[/bold bright_green]\n"
            "[bold bright_green]2. 开始学习[/bold bright_green]\n"
            "[bold bright_green]3. 登录/刷新[/bold bright_green]\n"
            "[bold bright_green]4. 登录状态[/bold bright_green]\n"
            "[bold bright_green]5. 功能说明[/bold bright_green]\n"
            "[bold bright_green]6. 初始化配置[/bold bright_green]\n"
            "[bold bright_green]7. 检测更新[/bold bright_green]\n"
            "[bold]0. 退出程序[/bold]",
            title="云上河开后台学习管理器",
            border_style="cyan",
        )
    )


def show_feature_help() -> None:
    """显示独立的详细功能说明，不重复主菜单。"""
    console.print(
        Panel(
            "[bold bright_green]1. 课程状态[/bold bright_green]\n"
            "实时从服务器读取课程、章节和 activity，并显示每项的序号、"
            "类型、服务器认可的进度与完成状态。这里的序号可作为学习起点。\n\n"
            "[bold bright_green]2. 开始学习[/bold bright_green]\n"
            "可直接从第一个未完成任务开始；选择指定序号时，会先显示一次"
            "课程状态列表，再输入起始序号并从该项向后查找"
            "未完成的资源任务。程序按顺序发送"
            " heartbeat、定期重查服务器进度，达到完成条件后确认结果并自动切换"
            "到下一项。已完成项会自动跳过；按 Ctrl+C 可安全中止。\n\n"
            "[bold bright_green]3. 登录/刷新[/bold bright_green]\n"
            "优先使用系统默认的 Chrome/Edge 内核启动独立临时窗口，不占用或锁定"
            "日常浏览器配置。在平台官方页面登录后，会话保存到 .cloudriver/session.json。"
            "首次使用、会话过期或平台持续返回 OFFLINE 时使用。\n\n"
            "[bold bright_green]4. 登录状态[/bold bright_green]\n"
            "向服务器确认当前 session 是否 ONLINE，并显示当前账户的姓名、学号/"
            "用户名、用户 ID 和账户角色。会话过期时会尝试静默刷新。\n\n"
            "[bold bright_green]5. 功能说明[/bold bright_green]\n"
            "显示当前这个详细说明页。\n\n"
            "[bold bright_green]6. 初始化配置[/bold bright_green]\n"
            "生成或整理 .cloudriver/config.json，用于设置 heartbeat 间隔、状态查询"
            "间隔、网络超时、并发策略和数据目录。此操作不会删除会话或学习进度。\n\n"
            "[bold bright_green]7. 检测更新[/bold bright_green]\n"
            "手动从 Git origin 检测新版本。只有发现可快进更新的新版本且你"
            "明确同意后，才会拉取代码、覆盖安装并自动重启。本地有未提交"
            "修改或分支分叉时会拒绝覆盖。\n\n"
            "[bold]0. 退出程序[/bold]\n"
            "结束当前程序。如果学习已被中止，下次启动会重新以服务器状态为准。",
            title="功能说明",
            border_style="magenta",
        )
    )


def first_run_setup(config: Path | None = None) -> None:
    """首次启动时给出必要指引，并可立即完成登录。"""
    settings = settings_for(config)
    if settings.session_file.exists():
        return
    console.print(
        Panel(
            "未检测到登录会话。\n\n"
            "• 程序会尽量使用系统默认的 Chrome 或 Edge 内核。\n"
            "• 登录窗口使用独立临时配置，不会锁定你日常使用的浏览器。\n"
            "• 账号和密码只在平台官方页面中输入。\n"
            "• 会话保存在 .cloudriver/session.json，请勿分享该文件。",
            title="首次启动",
            border_style="cyan",
        )
    )
    if not Confirm.ask("是否现在登录", default=True):
        return
    try:
        login(config, ticket_url=None)
    except typer.Exit:
        console.print("[yellow]本次未完成登录，可稍后在菜单选择“登录/刷新”。[/yellow]")


def interactive_menu(config: Path | None = None) -> None:
    """不带参数启动时显示的循环菜单。"""
    first_run_setup(config)
    while True:
        show_menu()
        try:
            choice = Prompt.ask(
                "请选择功能",
                choices=["0", "1", "2", "3", "4", "5", "6", "7"],
                default="1",
                show_choices=False,
            )
            if choice == "0":
                console.print("[cyan]已退出。[/cyan]")
                return
            if choice == "1":
                status(config)
            elif choice == "2":
                start_index = choose_start_index(config)
                run(config, once=False, start=start_index)
            elif choice == "3":
                login(config, ticket_url=None)
            elif choice == "4":
                login_status(config)
            elif choice == "5":
                show_feature_help()
                console.input("\n按 Enter 返回菜单")
            elif choice == "6":
                init(config)
            elif choice == "7":
                check_update(config, restart=True)
        except typer.Exit:
            # 子功能失败后返回菜单，不让整个启动器退出。
            pass
        except KeyboardInterrupt:
            console.print("\n[yellow]操作已取消，返回菜单。[/yellow]")


def choose_start_index(config: Path | None = None) -> int:
    """让用户选择直接开始，或指定课程序号。"""
    mode = Prompt.ask(
        "开始方式：1. 直接开始（第一个未完成）  2. 指定序号",
        choices=["1", "2"],
        default="1",
        show_choices=False,
    )
    if mode == "1":
        return 1
    status(config)
    while True:
        start_index = IntPrompt.ask("从哪个序号开始")
        if start_index >= 1:
            return start_index
        console.print("[red]起始序号必须大于等于 1。[/red]")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="菜单使用的配置 JSON 路径")
    ] = None,
) -> None:
    """不指定子命令时进入交互菜单。"""
    if ctx.invoked_subcommand is None:
        interactive_menu(config)
