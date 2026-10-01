from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import Settings
from .errors import CloudRiverError


def default_browser_id() -> str | None:
    """尽力识别系统默认浏览器；失败时由 Playwright 安全回退。"""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = (
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\https\UserChoice"
            )
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "ProgId")
                return str(value)
        except (OSError, ImportError):
            return None
    return os.environ.get("BROWSER") or None


def browser_channel_candidates(browser_id: str | None = None) -> list[str | None]:
    """优先系统默认的 Chromium 浏览器，最后回退到 Playwright Chromium。"""
    identity = (browser_id or default_browser_id() or "").lower()
    preferred: str | None = None
    if "edge" in identity:
        preferred = "msedge"
    elif "chrome" in identity:
        preferred = "chrome"

    system_fallbacks = (
        ["msedge", "chrome"] if sys.platform == "win32" else ["chrome", "msedge"]
    )
    candidates: list[str | None] = []
    for candidate in [preferred, *system_fallbacks, None]:
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def login_url(settings: Settings) -> str:
    # casLogin 会按精确 service 值校验 ST，必须使用前端配置中的无尾斜杠 URL。
    service = str(settings.front_url).rstrip("/")
    return f"{str(settings.cas_url)}?service={quote(service, safe='')}"


async def browser_login(settings: Settings) -> Path:
    """用隔离的临时浏览器会话登录，不占用用户日常浏览器配置。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise CloudRiverError(
            "缺少 Playwright：请安装 `pip install -e .[browser]` 并运行 "
            "`playwright install chromium`。"
        ) from exc

    settings.session_file.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = None
        selected = "Playwright Chromium"
        launch_errors: list[str] = []
        for channel in browser_channel_candidates():
            try:
                if channel is not None:
                    browser = await pw.chromium.launch(
                        channel=channel,
                        headless=settings.headless_login,
                    )
                else:
                    browser = await pw.chromium.launch(
                        headless=settings.headless_login
                    )
                selected = channel or selected
                break
            except Exception as exc:
                launch_errors.append(f"{channel or 'chromium'}: {type(exc).__name__}")
        if browser is None:
            tried = ", ".join(launch_errors)
            raise CloudRiverError(
                "未找到 Playwright 可驱动的系统浏览器；请运行 "
                "`python -m playwright install chromium`后重试。"
                f" 已尝试：{tried}"
            )
        try:
            context = await browser.new_context(user_agent=settings.user_agent)
            page = await context.new_page()
            await page.goto(login_url(settings), wait_until="domcontentloaded")
            print(
                f"已启动 {selected} 的独立临时窗口，不会锁定日常浏览器。\n"
                "请在官方页面完成登录；检测到 ONLINE 后会自动保存会话。"
            )
            deadline_ms = 10 * 60 * 1000
            await page.wait_for_url(f"{str(settings.front_url).rstrip('/')}**", timeout=deadline_ms)
            await page.wait_for_function(
                """async () => {
                    try {
                      const r = await fetch('/jxpt-app/user/isOnline');
                      const j = await r.json();
                      return j.code === 'ONLINE';
                    } catch (_) { return false; }
                }""",
                timeout=deadline_ms,
                polling=1000,
            )
            state = await context.storage_state()
            state["referer"] = page.url
            state["local_storage"] = await page.evaluate("() => ({...localStorage})")
            settings.session_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            raise CloudRiverError("登录超时或平台未返回 ONLINE。") from exc
        finally:
            await browser.close()
    return settings.session_file


async def ticket_login(settings: Settings, ticket_url: str) -> Path:
    """用用户刚复制的 CAS 回跳 URL 建立会话；ticket 通常只能使用一次。"""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=settings.request_timeout_seconds,
        headers={"User-Agent": settings.user_agent},
    ) as client:
        response = await client.get(ticket_url)
        response.raise_for_status()
        ticket = response.url.params.get("ticket") or httpx.URL(ticket_url).params.get("ticket")
        if not ticket:
            raise CloudRiverError("回跳 URL 中没有 ticket。")
        # 首页是静态页面；纯 HTTP 客户端必须显式复现前端 casLogin 调用。
        cas_login = await client.get(
            f"{str(settings.base_url).rstrip('/')}{settings.api.cas_login}",
            params={"ticket": ticket},
            headers={"Referer": str(response.url)},
        )
        cas_payload = cas_login.json()
        if cas_payload.get("code") != "SUCCESS":
            raise CloudRiverError(f"casLogin 失败：{cas_payload.get('code')}")
        online = await client.get(f"{str(settings.base_url).rstrip('/')}{settings.api.online}")
        payload = online.json()
        if payload.get("code") != "ONLINE":
            raise CloudRiverError(f"ticket 未建立有效会话：{payload!r}")
        state = {
            "cookies": [
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
                for cookie in client.cookies.jar
            ],
            "origins": [],
            "referer": str(response.url),
            "local_storage": {},
        }
    settings.session_file.parent.mkdir(parents=True, exist_ok=True)
    settings.session_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    return settings.session_file


def load_session(path: Path) -> tuple[httpx.Cookies, str | None, dict[str, str]]:
    if not path.exists():
        raise CloudRiverError(f"会话文件不存在：{path}；请先运行 `cloudriver login`。")
    state = json.loads(path.read_text(encoding="utf-8"))
    cookies = httpx.Cookies()
    for item in state.get("cookies", []):
        if not item.get("name"):
            continue
        cookies.set(
            item["name"], item["value"], domain=item.get("domain"), path=item.get("path", "/")
        )
    return cookies, state.get("referer"), state.get("local_storage", {})


def persist_httpx_session(
    path: Path,
    cookies: httpx.Cookies,
    referer: str,
    local_storage: dict[str, str],
) -> None:
    """保存静默 CAS 刷新后的 cookie，同时保留本地前端状态。"""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"origins": []}
    state["cookies"] = [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "expires": cookie.expires if cookie.expires is not None else -1,
            "httpOnly": bool(cookie.has_nonstandard_attr("HttpOnly")),
            "secure": cookie.secure,
            "sameSite": "Lax",
        }
        for cookie in cookies.jar
        if cookie.name
    ]
    state["referer"] = referer
    state["local_storage"] = local_storage
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
