from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cloudriver_manager.updater import GitUpdater, UpdateError


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def create_update_fixture(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    target = tmp_path / "target"
    remote.mkdir()
    seed.mkdir()
    git(remote, "init", "--bare")
    git(seed, "init")
    git(seed, "config", "user.email", "tests@example.invalid")
    git(seed, "config", "user.name", "Tests")
    (seed / "pyproject.toml").write_text(
        '[project]\nname = "cloudriver-manager"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (seed / "version.txt").write_text("one", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "initial")
    git(seed, "branch", "-M", "main")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-u", "origin", "main")
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    git(tmp_path, "clone", str(remote), str(target))
    return seed, target


def test_git_updater_detects_and_applies_fast_forward(tmp_path):
    seed, target = create_update_fixture(tmp_path)
    (seed / "version.txt").write_text("two", encoding="utf-8")
    git(seed, "add", "version.txt")
    git(seed, "commit", "-m", "release two")
    git(seed, "push")

    updater = GitUpdater(target)
    info = updater.check()
    assert info.available
    assert any("release two" in line for line in info.changes)

    updater.apply(info, install=False)
    assert (target / "version.txt").read_text(encoding="utf-8") == "two"
    assert not updater.check().available


def test_git_updater_refuses_to_overwrite_tracked_changes(tmp_path):
    seed, target = create_update_fixture(tmp_path)
    (seed / "version.txt").write_text("remote", encoding="utf-8")
    git(seed, "add", "version.txt")
    git(seed, "commit", "-m", "remote update")
    git(seed, "push")

    updater = GitUpdater(target)
    info = updater.check()
    (target / "version.txt").write_text("local", encoding="utf-8")

    with pytest.raises(UpdateError, match="未提交"):
        updater.apply(info, install=False)
