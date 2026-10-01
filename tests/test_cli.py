from pathlib import Path

from rich.text import Text
from typer.testing import CliRunner

from cloudriver_manager.cli import (
    app,
    choose_start_index,
    completion_cell,
    parse_server_progress,
    show_feature_help,
    show_menu,
)
from cloudriver_manager.cli import console as cli_console
from cloudriver_manager.updater import UpdateInfo


def test_completion_cells_use_distinct_colors():
    yes = completion_cell(True)
    no = completion_cell(False)
    assert isinstance(yes, Text) and str(yes) == "是" and "green" in str(yes.style)
    assert isinstance(no, Text) and str(no) == "否" and "red" in str(no.style)


def test_no_argument_launch_opens_menu_and_can_exit(tmp_path):
    config = tmp_path / "config.json"
    result = CliRunner().invoke(
        app, ["--config", str(config)], input="n\n0\n"
    )
    assert result.exit_code == 0
    assert "首次启动" in result.stdout
    assert "云上河开后台学习管理器" in result.stdout
    assert "请选择功能" in result.stdout
    assert "绿色“是”" not in result.stdout


def test_first_run_can_login_immediately(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "cloudriver_manager.cli.login",
        lambda config=None, ticket_url=None: calls.append((config, ticket_url)),
    )
    config = tmp_path / "config.json"
    result = CliRunner().invoke(
        app, ["--config", str(config)], input="y\n0\n"
    )

    assert result.exit_code == 0
    assert calls == [(config, None)]


def test_menu_is_concise_and_feature_help_is_detailed():
    with cli_console.capture() as capture:
        show_menu()
    menu_output = capture.get()
    assert "1. 课程状态" in menu_output
    assert "4. 登录状态" in menu_output
    assert "7. 检测更新" in menu_output
    assert "heartbeat 间隔" not in menu_output
    assert "服务器仍判定未完成" not in menu_output

    with cli_console.capture() as capture:
        show_feature_help()
    help_output = capture.get()
    assert "起点" in help_output
    assert "Ctrl+C 可安全中止" in help_output
    assert ".cloudriver/session.json" in help_output


def test_run_rejects_invalid_start_index():
    result = CliRunner().invoke(app, ["run", "--start", "0"])
    assert result.exit_code != 0


def test_choose_start_index_supports_direct_and_custom(monkeypatch):
    status_calls = []
    monkeypatch.setattr(
        "cloudriver_manager.cli.status", lambda config=None: status_calls.append(config)
    )
    monkeypatch.setattr(
        "cloudriver_manager.cli.Prompt.ask", lambda *args, **kwargs: "1"
    )
    assert choose_start_index() == 1
    assert status_calls == []

    monkeypatch.setattr(
        "cloudriver_manager.cli.Prompt.ask", lambda *args, **kwargs: "2"
    )
    monkeypatch.setattr(
        "cloudriver_manager.cli.IntPrompt.ask", lambda *args, **kwargs: 18
    )
    config_path = Path("custom-config.json")
    assert choose_start_index(config_path) == 18
    assert status_calls == [config_path]


def test_login_automatically_displays_account(monkeypatch):
    async def fake_login(_settings):
        return Path(".cloudriver/session.json")

    async def fake_account(_settings):
        return {
            "realName": "张三",
            "userName": "2026000001",
            "userId": "user-1",
            "role": "STU",
        }

    monkeypatch.setattr("cloudriver_manager.cli.browser_login", fake_login)
    monkeypatch.setattr("cloudriver_manager.cli.fetch_current_account", fake_account)
    result = CliRunner().invoke(app, ["login"])

    assert result.exit_code == 0
    assert "会话已保存" in result.stdout
    assert "当前账户" in result.stdout
    assert "张三" in result.stdout
    assert "2026000001" in result.stdout


def test_check_update_applies_only_after_confirmation(tmp_path, monkeypatch):
    applied = []
    info = UpdateInfo(
        root=tmp_path,
        remote_url="https://github.com/example/project.git",
        upstream="origin/main",
        current_commit="a" * 40,
        latest_commit="b" * 40,
        available=True,
        changes=("bbbbbbbb update",),
    )

    class FakeUpdater:
        def check(self):
            return info

        def apply(self, value):
            applied.append(value)

    monkeypatch.setattr("cloudriver_manager.cli.GitUpdater", FakeUpdater)
    result = CliRunner().invoke(
        app, ["check-update", "--no-restart"], input="y\n"
    )

    assert result.exit_code == 0
    assert applied == [info]
    assert "发现新版本" in result.stdout
    assert "更新完成" in result.stdout


def test_parse_server_progress():
    assert parse_server_progress("我的书院——润心书院: 服务器 260/513 秒") == (
        "我的书院——润心书院",
        260.0,
        513.0,
    )
    assert parse_server_progress("服务器已确认完成") is None
