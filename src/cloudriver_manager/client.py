from __future__ import annotations

import json
import random
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from .auth import load_session, persist_httpx_session
from .config import Settings
from .errors import (
    ApiError,
    LearningDeadlinePassed,
    ParallelLearningRejected,
    SessionExpired,
)
from .models import ActivityStatus, ApiEnvelope, Course, Unit


RETRYABLE = (httpx.TransportError, httpx.TimeoutException)
TOKEN_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZabcdefhijkmnprstwxyz2345678"


def random_token(length: int = 8) -> str:
    return "".join(random.SystemRandom().choice(TOKEN_ALPHABET) for _ in range(length))


class PlatformClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        cookies, referer, local_storage = load_session(settings.session_file)
        self.referer = referer or str(settings.front_url)
        self.local_storage = local_storage
        self.token = local_storage.get("heartBeatToken") or random_token()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": settings.user_agent,
            "Referer": self.referer,
        }
        self.http = httpx.AsyncClient(
            base_url=str(settings.base_url),
            cookies=cookies,
            headers=headers,
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> "PlatformClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.http.aclose()

    def account_info(self) -> dict[str, str]:
        """返回当前会话在 casLogin 时由服务器下发的账户信息。"""
        result: dict[str, str] = {}
        for key in ("realName", "userName", "userId", "role"):
            value = self.local_storage.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        return result

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        wait=wait_exponential_jitter(initial=0.5, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def _request(self, method: str, path: str, **kwargs: Any) -> ApiEnvelope:
        response = await self.http.request(method, path, **kwargs)
        if response.status_code >= 500:
            raise httpx.TransportError(f"server {response.status_code}")
        response.raise_for_status()
        try:
            envelope = ApiEnvelope.model_validate(response.json())
        except Exception as exc:
            # 登录页 HTML 被 200 返回也视为 session 失效。
            if "text/html" in response.headers.get("content-type", ""):
                raise SessionExpired("服务器返回了登录页。") from exc
            raise ApiError("INVALID_RESPONSE", response.text[:300]) from exc
        if envelope.code == "OFFLINE":
            raise SessionExpired("平台返回 OFFLINE。")
        if envelope.code == "UNTIMED":
            raise ParallelLearningRejected("平台检测到多活跃学习任务。")
        if envelope.code == "OVERDEADLINE":
            raise LearningDeadlinePassed("课程学习期限已结束。")
        return envelope

    async def check_online(self, *, auto_renew: bool = True) -> bool:
        try:
            envelope = await self._request("GET", self.settings.api.online)
        except SessionExpired:
            if not auto_renew or not await self.silent_cas_login():
                raise
            envelope = await self._request("GET", self.settings.api.online)
        if envelope.code == "ARREARAGE":
            raise ApiError(envelope.code, envelope.body)
        return envelope.code == "ONLINE"

    async def silent_cas_login(self, *, rotate_heartbeat_token: bool = False) -> bool:
        """用保存的 CASTGC 申请新 ST，再执行网站前端的 casLogin 初始化。"""
        service = str(self.settings.front_url).rstrip("/")
        cas_url = f"{str(self.settings.cas_url)}?service={quote(service, safe='')}"
        try:
            landing = await self.http.get(cas_url)
            ticket = landing.url.params.get("ticket")
            if not ticket:
                return False
            response = await self.http.get(
                self.settings.api.cas_login,
                params={"ticket": ticket},
                headers={"Referer": str(landing.url)},
            )
            envelope = ApiEnvelope.model_validate(response.json())
            if envelope.code != "SUCCESS":
                return False
            if rotate_heartbeat_token:
                self.token = random_token()
                self.local_storage["heartBeatToken"] = self.token
            self.referer = str(landing.url)
            self.http.headers["Referer"] = self.referer
            if isinstance(envelope.body, dict):
                for key in (
                    "userId",
                    "userName",
                    "realName",
                    "verifyStatus",
                    "role",
                    "onlineTime",
                    "courseUsers",
                ):
                    if key in envelope.body:
                        value = envelope.body[key]
                        self.local_storage[key] = (
                            value
                            if isinstance(value, str)
                            else json.dumps(value, ensure_ascii=False)
                        )
            persist_httpx_session(
                self.settings.session_file,
                self.http.cookies,
                self.referer,
                self.local_storage,
            )
            return True
        except (httpx.HTTPError, ValueError):
            return False

    async def reset_api_session(self) -> bool:
        """清理服务器残留的活跃学习会话，并用 CAS 建立一个新的串行会话。"""
        try:
            await self.http.get("/user/logout")
        except httpx.HTTPError:
            return False
        return await self.silent_cas_login(rotate_heartbeat_token=True)

    async def list_courses(self) -> list[Course]:
        active = await self._request("GET", self.settings.api.current_courses)
        finished = await self._request("GET", self.settings.api.finished_courses)
        if active.code != "SUCCESS":
            raise ApiError(active.code, active.body)
        if finished.code != "SUCCESS":
            raise ApiError(finished.code, finished.body)
        courses: dict[str, Course] = {}
        for item in active.body or []:
            course = Course.model_validate({**item, "finished": False})
            courses[course.course_version_id] = course
        for item in finished.body or []:
            course = Course.model_validate({**item, "finished": True})
            courses.setdefault(course.course_version_id, course)
        return list(courses.values())

    async def list_units(self, course_version_id: str) -> list[Unit]:
        path = self.settings.api.activities.format(course_version_id=course_version_id)
        envelope = await self._request("GET", path)
        if envelope.code not in {"LIST_VALUE", "SUCCESS"}:
            raise ApiError(envelope.code, envelope.body)
        units = [Unit.model_validate(item) for item in (envelope.body or [])]
        for unit in units:
            for activity in unit.activitys:
                activity.course_version_id = activity.course_version_id or course_version_id
        return units

    async def activity_status(self, course_version_id: str, activity_id: str) -> ActivityStatus:
        envelope = await self._request(
            "GET",
            self.settings.api.display,
            params={"courseVersionId": course_version_id, "activityId": activity_id},
        )
        if envelope.code != "SUCCESS" or not envelope.body:
            raise ApiError(envelope.code, envelope.body)
        return ActivityStatus.model_validate(envelope.body)

    async def heartbeat(
        self,
        status: ActivityStatus,
        time_point: float,
        *,
        play_status: bool = True,
        raise_on_untimed: bool = True,
    ) -> ApiEnvelope:
        resource_type = heartbeat_type(status)
        params: dict[str, Any] = {
            "courseVersionId": status.course_version_id,
            "activityId": status.activity_id,
            "token": self.token,
            "type": resource_type,
            "isStuLearningRecord": 2,
        }
        if resource_type in {2, 6}:
            params.update(
                playStatus=str(play_status).lower(),
                isResourcePage="true",
                timePoint=max(time_point, 0.0),
            )
        try:
            envelope = await self._request("GET", self.settings.api.heartbeat, params=params)
        except ParallelLearningRejected:
            if raise_on_untimed:
                raise
            return ApiEnvelope(code="UNTIMED", body=None, error=False)
        if envelope.code != "SUCCESS":
            raise ApiError(envelope.code, envelope.body)
        return envelope

    async def finish_activity(self, course_version_id: str, activity_id: str) -> None:
        envelope = await self._request(
            "POST",
            self.settings.api.finish,
            data={"activityId": activity_id, "courseVersionId": course_version_id},
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        )
        if envelope.code != "SUCCESS":
            raise ApiError(envelope.code, envelope.body)


def heartbeat_type(status: ActivityStatus) -> int:
    mime = status.resource.mime_type.lower()
    video_formats = {"mp4", "avi", "ts", "flv", "mov", "mpg", "wmv", "rm", "f4v", "rmvb"}
    if status.type == 2 or mime in video_formats:
        return 2
    if status.type == 7 or mime in {"mp3", "wav"}:
        return 6
    return 1
