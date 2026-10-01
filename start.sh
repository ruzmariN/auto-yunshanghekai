#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

find_python() {
  for candidate in python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && \
      "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "未检测到 Python 3.11 或更高版本，请先安装 Python 后再运行。"
  echo "详细步骤请查看“小白教程.md”。"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "[首次启动 1/3] 正在创建本项目专用的 Python 运行环境，请勿关闭终端……"
  "$PYTHON" -m venv .venv
elif ! .venv/bin/python --version >/dev/null 2>&1; then
  echo "[启动准备 1/3] 检测到运行环境异常，正在自动修复……"
  "$PYTHON" -m venv --clear .venv
fi

if ! .venv/bin/python -c 'import cloudriver_manager, playwright' >/dev/null 2>&1; then
  echo "[首次启动 2/3] 正在安装程序依赖和登录组件……"
  echo "              \033[38;5;82m安装速度取决于网速，请耐心等一下。\033[0m"
  sleep 0.3
  echo "              \033[38;5;82m安装内容仅供本项目使用，不会修改浏览器或保存平台密码。\033[0m"
  .venv/bin/python -m pip install --disable-pip-version-check --progress-bar off --quiet -e '.[browser]'
  echo "[首次启动 3/3] 安装完成，正在进入程序菜单……"
  echo
fi

exec .venv/bin/python -m cloudriver_manager