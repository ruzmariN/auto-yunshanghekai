from __future__ import annotations

import json

import httpx
import pytest

from cloudriver_manager.client import PlatformClient


@pytest.mark.asyncio
async def test_account_info_uses_identity_from_current_session(settings):
    async with PlatformClient(settings) as client:
        client.local_storage.update(
            {
                "realName": "张三",
                "userName": "2026000001",
                "userId": "user-1",
                "role": "STU",
                "unused": "ignore-me",
            }
        )
        assert client.account_info() == {
            "realName": "张三",
            "userName": "2026000001",
            "userId": "user-1",
            "role": "STU",
        }


@pytest.mark.asyncio
async def test_discovers_courses_units_and_server_status(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("getCurrentCourseListByStudent"):
            body = {
                "error": False,
                "code": "SUCCESS",
                "body": [{"courseVersionId": "c1", "courseName": "课程"}],
            }
        elif path.endswith("getAllFinishCourseByStudent"):
            body = {"error": False, "code": "SUCCESS", "body": []}
        elif path.endswith("getAllActivitys/c1"):
            body = {
                "error": False,
                "code": "LIST_VALUE",
                "body": [
                    {
                        "unit": "第一章",
                        "activitys": [
                            {
                                "activityId": "a1",
                                "activityName": "视频",
                                "type": 2,
                                "finish": 0,
                            }
                        ],
                    }
                ],
            }
        elif path.endswith("/display"):
            body = {
                "error": False,
                "code": "SUCCESS",
                "body": {
                    "type": 2,
                    "courseVersionId": "c1",
                    "activityId": "a1",
                    "timePoint": 5,
                    "resource": {"length": 20, "mimeType": "mp4"},
                    "resourceFinished": 0,
                    "stuResourceTotalTime": 10,
                    "homeworkNum": 0,
                },
            }
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=body)

    async with PlatformClient(settings, transport=httpx.MockTransport(handler)) as client:
        courses = await client.list_courses()
        units = await client.list_units("c1")
        remote = await client.activity_status("c1", "a1")
    assert courses[0].course_name == "课程"
    assert units[0].activitys[0].activity_id == "a1"
    assert remote.progress_seconds == 10
    assert not remote.server_finished


@pytest.mark.asyncio
async def test_heartbeat_matches_observed_har_query(settings):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"error": False, "code": "SUCCESS", "body": "ABCDEFGH"})

    status_payload = {
        "type": 2,
        "courseVersionId": "c1",
        "activityId": "a1",
        "resource": {"length": 30, "mimeType": "mp4"},
    }
    from cloudriver_manager.models import ActivityStatus

    async with PlatformClient(settings, transport=httpx.MockTransport(handler)) as client:
        await client.heartbeat(ActivityStatus.model_validate(status_payload), 12.5)
    assert seen == {
        "courseVersionId": "c1",
        "activityId": "a1",
        "token": "ABCDEFGH",
        "type": "2",
        "isStuLearningRecord": "2",
        "playStatus": "true",
        "isResourcePage": "true",
        "timePoint": "12.5",
    }


@pytest.mark.asyncio
async def test_offline_session_is_renewed_through_cas_login(settings):
    calls = {"online": 0, "cas_login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "authserver.open.ha.cn":
            return httpx.Response(
                302,
                headers={"Location": "https://jxapp.open.ha.cn?ticket=ST-test"},
            )
        if path == "/":
            return httpx.Response(200, text="<html></html>")
        if path.endswith("/user/casLogin"):
            calls["cas_login"] += 1
            assert request.url.params["ticket"] == "ST-test"
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "code": "SUCCESS",
                    "body": {"userId": "u1", "role": "STU"},
                },
            )
        if path.endswith("/user/isOnline"):
            calls["online"] += 1
            code = "OFFLINE" if calls["online"] == 1 else "ONLINE"
            return httpx.Response(200, json={"error": False, "code": code, "body": None})
        raise AssertionError(str(request.url))

    async with PlatformClient(settings, transport=httpx.MockTransport(handler)) as client:
        assert await client.check_online()
    assert calls == {"online": 2, "cas_login": 1}


@pytest.mark.asyncio
async def test_reset_api_session_logs_out_and_rotates_heartbeat_token(settings):
    calls = {"logout": 0, "cas_login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "authserver.open.ha.cn":
            return httpx.Response(
                302,
                headers={"Location": "https://jxapp.open.ha.cn?ticket=ST-reset"},
            )
        if request.url.path == "/":
            return httpx.Response(200, text="<html></html>")
        if request.url.path.endswith("/user/logout"):
            calls["logout"] += 1
            return httpx.Response(200, json={"error": False, "code": "SUCCESS", "body": None})
        if request.url.path.endswith("/user/casLogin"):
            calls["cas_login"] += 1
            return httpx.Response(
                200,
                json={"error": False, "code": "SUCCESS", "body": {"userId": "u1"}},
            )
        raise AssertionError(str(request.url))

    async with PlatformClient(settings, transport=httpx.MockTransport(handler)) as client:
        old_token = client.token
        assert await client.reset_api_session()
        assert client.token != old_token
        assert client.local_storage["heartBeatToken"] == client.token
    assert calls == {"logout": 1, "cas_login": 1}
