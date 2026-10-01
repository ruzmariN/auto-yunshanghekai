from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import CloudRiverError


class UpdateError(CloudRiverError):
    """自动更新无法安全继续。"""


@dataclass(frozen=True)
class UpdateInfo:
    root: Path
    remote_url: str
    upstream: str
    current_commit: str
    latest_commit: str
    available: bool
    local_ahead: bool = False
    changes: tuple[str, ...] = ()


def _project_matches(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return payload.get("project", {}).get("name") == "cloudriver-manager"


def _redact_remote(url: str) -> str:
    """避免将 HTTPS remote 中可能嵌入的凭据打印到终端。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


class GitUpdater:
    """仅对干净的 Git 检出执行 origin 快进更新。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root.resolve() if root else self._find_root()

    @staticmethod
    def _run(
        args: list[str],
        *,
        cwd: Path,
        check: bool = True,
        timeout: int = 120,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                check=False,
                capture_output=capture,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise UpdateError("未找到 Git，请先安装 Git 后重试。") from exc
        except subprocess.TimeoutExpired as exc:
            raise UpdateError("更新操作超时，请检查网络后重试。") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "Git 命令失败").strip()
            raise UpdateError(detail)
        return result

    @classmethod
    def _find_root(cls) -> Path:
        candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
        checked: set[Path] = set()
        for candidate in candidates:
            candidate = candidate.resolve()
            if candidate in checked:
                continue
            checked.add(candidate)
            result = cls._run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=candidate,
                check=False,
            )
            if result.returncode != 0:
                continue
            root = Path(result.stdout.strip()).resolve()
            if _project_matches(root):
                return root
        raise UpdateError(
            "当前程序不是 Git 检出。自动更新需要使用 `git clone` "
            "下载的项目，并保留 origin 远程仓库。"
        )

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._run(["git", *args], cwd=self.root, check=check)

    def _upstream(self) -> str:
        result = self._git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        result = self._git(
            "symbolic-ref",
            "--short",
            "refs/remotes/origin/HEAD",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        raise UpdateError("当前分支没有上游分支，无法确定要更新到哪个版本。")

    def check(self) -> UpdateInfo:
        remote = self._git("remote", "get-url", "origin").stdout.strip()
        self._git("fetch", "--quiet", "--prune", "origin")
        upstream = self._upstream()
        current = self._git("rev-parse", "HEAD").stdout.strip()
        latest = self._git("rev-parse", upstream).stdout.strip()
        if current == latest:
            return UpdateInfo(
                self.root,
                _redact_remote(remote),
                upstream,
                current,
                latest,
                available=False,
            )

        remote_contains_local = self._git(
            "merge-base", "--is-ancestor", current, latest, check=False
        ).returncode == 0
        local_contains_remote = self._git(
            "merge-base", "--is-ancestor", latest, current, check=False
        ).returncode == 0
        if not remote_contains_local and not local_contains_remote:
            raise UpdateError(
                "本地分支已与远程分支分叉，为避免覆盖本地工作，已取消自动更新。"
            )
        if local_contains_remote:
            return UpdateInfo(
                self.root,
                _redact_remote(remote),
                upstream,
                current,
                latest,
                available=False,
                local_ahead=True,
            )

        log = self._git(
            "log", "--oneline", "--no-decorate", "-n", "8", f"{current}..{latest}"
        ).stdout.splitlines()
        return UpdateInfo(
            self.root,
            _redact_remote(remote),
            upstream,
            current,
            latest,
            available=True,
            changes=tuple(line.strip() for line in log if line.strip()),
        )

    def apply(self, info: UpdateInfo, *, install: bool = True) -> None:
        if info.root != self.root or not info.available:
            raise UpdateError("没有可安装的更新。")
        if self._git("rev-parse", "HEAD").stdout.strip() != info.current_commit:
            raise UpdateError("检测后本地版本已变化，请重新检测更新。")
        tracked_changes = self._git(
            "status", "--porcelain", "--untracked-files=no"
        ).stdout.strip()
        if tracked_changes:
            raise UpdateError(
                "检测到未提交的已跟踪文件修改。为避免覆盖本地工作，已取消更新。"
            )

        self._git("merge", "--ff-only", info.latest_commit)
        if not install:
            return
        install_result = self._run(
            [sys.executable, "-m", "pip", "install", "-e", ".[browser]"],
            cwd=self.root,
            check=False,
            timeout=600,
            capture=False,
        )
        if install_result.returncode == 0:
            return

        # 更新前已确认工作树干净，因此可安全回滚这次快进更新。
        self._git("reset", "--hard", info.current_commit)
        raise UpdateError("新版依赖安装失败，源代码已自动回滚到更新前版本。")
