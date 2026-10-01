from __future__ import annotations

import httpx
import pytest

from cloudriver_manager.client import PlatformClient
from cloudriver_manager.engine import LearningEngine
from cloudriver_manager.models import Activity, ActivityStatus, Course, Unit
from cloudriver_manager.state import CheckpointStore


@pytest.mark.asyncio
async def test_single_activity_closes_loop_with_server_confirmation(settings, tmp_path):
    counts = {"heartbeat": 0, "finish": 0, "display": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/heartbeat"):
            counts["heartbeat"] += 1
            payload = {"error": False, "code": "SUCCESS", "body": "ABCDEFGH"}
        elif path.endswith("/finishActivity"):
            counts["finish"] += 1
            payload = {"error": False, "code": "SUCCESS", "body": "ok"}
        elif path.endswith("/display"):
            counts["display"] += 1
            done = counts["finish"] > 0
            payload = {
                "error": False,
                "code": "SUCCESS",
                "body": {
                    "type": 2,
                    "courseVersionId": "c1",
                    "activityId": "a1",
                    "timePoint": 1,
                    "resource": {"length": 0.01, "mimeType": "mp4"},
                    "resourceFinished": 1 if done else 0,
                    "stuResourceTotalTime": 1,
                    "homeworkNum": 0,
                },
            }
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=payload)

    initial = ActivityStatus.model_validate(
        {
            "type": 2,
            "courseVersionId": "c1",
            "activityId": "a1",
            "resource": {"length": 0.01, "mimeType": "mp4"},
            "resourceFinished": 0,
            "stuResourceTotalTime": 0,
            "homeworkNum": 0,
        }
    )
    async with PlatformClient(settings, transport=httpx.MockTransport(handler)) as client:
        engine = LearningEngine(client, settings, CheckpointStore(tmp_path / "checkpoint.json"))
        completed = await engine.learn_one(
            Course(courseVersionId="c1", courseName="课程"),
            Activity(activityId="a1", activityName="视频", type=2),
            initial,
        )
    assert completed
    assert counts["heartbeat"] >= 1
    assert counts["finish"] == 1
    assert counts["display"] >= 2
    assert "c1:a1" in engine.checkpoint.completed


@pytest.mark.asyncio
async def test_start_index_skips_earlier_and_completed_activities(settings, tmp_path):
    class FakeClient:
        token = "token"

        async def list_courses(self):
            return [Course(courseVersionId="c1", courseName="课程")]

        async def list_units(self, _course_version_id: str):
            return [
                Unit(
                    activitys=[
                        Activity(activityId="a1", activityName="序号1", type=2),
                        Activity(activityId="a2", activityName="序号2已完成", type=2, finish=1),
                        Activity(activityId="a3", activityName="序号3未完成", type=2),
                    ]
                )
            ]

        async def activity_status(self, course_version_id: str, activity_id: str):
            assert (course_version_id, activity_id) == ("c1", "a3")
            return ActivityStatus.model_validate(
                {
                    "type": 2,
                    "courseVersionId": "c1",
                    "activityId": "a3",
                    "resource": {"length": 100, "mimeType": "mp4"},
                    "resourceFinished": 0,
                }
            )

    engine = LearningEngine(
        FakeClient(),  # type: ignore[arg-type]
        settings,
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    pending = await engine.verified_pending(start_index=2)

    assert len(pending) == 1
    assert pending[0][1].activity_id == "a3"
    assert pending[0][3] == 3
