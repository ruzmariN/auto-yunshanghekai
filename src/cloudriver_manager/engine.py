from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from .client import PlatformClient, heartbeat_type
from .config import Settings
from .errors import ActivityBlocked, LearningDeadlinePassed, ParallelLearningRejected
from .models import Activity, ActivityStatus, Checkpoint, CheckpointItem, Course
from .state import CheckpointStore

ProgressCallback = Callable[[str, str], None]


class LearningEngine:
    def __init__(
        self,
        client: PlatformClient,
        settings: Settings,
        store: CheckpointStore,
        notify: ProgressCallback | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.store = store
        self.checkpoint = store.load()
        self.notify = notify or (lambda _level, _message: None)
        self.log = logging.getLogger(__name__)

    async def discover(self) -> list[tuple[int, Course, Activity]]:
        """按服务器目录顺序返回所有 activity 及其稳定序号。"""
        result: list[tuple[int, Course, Activity]] = []
        ordinal = 0
        for course in await self.client.list_courses():
            for unit in await self.client.list_units(course.course_version_id):
                for activity in unit.activitys:
                    ordinal += 1
                    result.append((ordinal, course, activity))
        return result

    async def verified_pending(
        self, start_index: int = 1
    ) -> list[tuple[Course, Activity, ActivityStatus, int]]:
        pending: list[tuple[Course, Activity, ActivityStatus, int]] = []
        for ordinal, course, activity in await self.discover():
            if ordinal < start_index:
                continue
            # 课程和 activity 完成值都来自本轮服务器目录响应。
            if course.finished or activity.is_catalog_finished:
                continue
            if not activity.is_resource_activity:
                self.notify(
                    "warning",
                    f"已识别未完成交互任务 [{ordinal}]："
                    f"{course.course_name} / {activity.activity_name} "
                    f"(type={activity.type})；需正常作答/发帖，未伪造完成。",
                )
                continue
            status = await self.client.activity_status(
                course.course_version_id, activity.activity_id
            )
            if not status.server_finished:
                pending.append((course, activity, status, ordinal))
        return pending

    async def probe_parallelism(
        self, tasks: list[tuple[Course, Activity, ActivityStatus, int]]
    ) -> int:
        if not self.settings.probe_parallelism or self.settings.max_workers == 1 or len(tasks) < 2:
            return 1
        first, second = tasks[:2]
        self.notify("info", "正在用两个独立 activity 各发送一次正常 heartbeat 探测并发能力…")
        replies = await asyncio.gather(
            self.client.heartbeat(first[2], first[2].time_point, raise_on_untimed=False),
            self.client.heartbeat(second[2], second[2].time_point, raise_on_untimed=False),
            return_exceptions=True,
        )
        if any(
            isinstance(item, ParallelLearningRejected)
            or (not isinstance(item, BaseException) and item.code == "UNTIMED")
            for item in replies
        ):
            self.notify("warning", "服务器拒绝多活跃任务，已自动降级为串行。")
            return 1
        if any(isinstance(item, BaseException) for item in replies):
            self.notify("warning", "并发探测遇到网络错误，保守采用串行。")
            return 1
        return min(self.settings.max_workers, len(tasks))

    async def learn_one(
        self,
        course: Course,
        activity: Activity,
        initial: ActivityStatus,
        ordinal: int | None = None,
    ) -> bool:
        key = f"{course.course_version_id}:{activity.activity_id}"
        status = initial
        if status.server_finished:
            return True
        if status.over_end_time:
            raise LearningDeadlinePassed(activity.activity_name)
        if status.homework_num:
            raise ActivityBlocked(f"{activity.activity_name} 含关联作业，需按前端流程先完成作业。")

        self.checkpoint.active[key] = CheckpointItem(
            course_version_id=course.course_version_id,
            activity_id=activity.activity_id,
            token=self.client.token,
            last_server_time_point=status.time_point,
            last_server_total_time=status.stu_resource_total_time,
        )
        self.store.save(self.checkpoint)
        prefix = f"[{ordinal}] " if ordinal is not None else ""
        self.notify("start", f"{prefix}{course.course_name} / {activity.activity_name}")
        self.notify(
            "progress",
            f"{activity.activity_name}: 服务器 {status.progress_seconds:.0f}/"
            f"{status.required_seconds:.0f} 秒",
        )

        local_time_point = status.time_point
        last_poll = 0.0
        loop = asyncio.get_running_loop()
        started = loop.time()
        while not status.server_finished:
            interval = self.settings.heartbeat_seconds
            await asyncio.sleep(interval)
            elapsed = loop.time() - started
            if heartbeat_type(status) in {2, 6}:
                # 与真实播放器相同：只能按实际流逝时间从服务器 timePoint 向前推进。
                local_time_point = min(
                    status.required_seconds,
                    max(local_time_point, initial.time_point + elapsed),
                )
            await self.client.heartbeat(status, local_time_point, play_status=True)

            now = loop.time()
            if now - last_poll >= self.settings.status_poll_seconds:
                status = await self.client.activity_status(
                    course.course_version_id, activity.activity_id
                )
                last_poll = now
                item = self.checkpoint.active[key]
                item.last_server_time_point = status.time_point
                item.last_server_total_time = status.stu_resource_total_time
                item.updated_at = datetime.now(timezone.utc)
                self.store.save(self.checkpoint)
                self.notify(
                    "progress",
                    f"{activity.activity_name}: 服务器 {status.progress_seconds:.0f}/"
                    f"{status.required_seconds:.0f} 秒",
                )

            if status.normal_completion_reached and not status.server_finished:
                await self.client.finish_activity(course.course_version_id, activity.activity_id)
                # finish 接口成功不是最终真值；必须再次查询 display。
                for _ in range(4):
                    await asyncio.sleep(1)
                    status = await self.client.activity_status(
                        course.course_version_id, activity.activity_id
                    )
                    if status.server_finished:
                        break

            if status.required_seconds <= 0 and not status.server_finished:
                raise ActivityBlocked(f"{activity.activity_name} 的服务器 resource.length 无效。")

        self.checkpoint.active.pop(key, None)
        if key not in self.checkpoint.completed:
            self.checkpoint.completed.append(key)
        self.store.save(self.checkpoint)
        self.notify("done", f"服务器已确认完成：{activity.activity_name}")
        return True

    async def run(self, once: bool = False, start_index: int = 1) -> None:
        if start_index < 1:
            raise ValueError("start_index 必须大于等于 1")
        serial_conflict_recoveries = 0
        self.notify("info", f"从序号 {start_index} 开始，已完成任务将自动跳过。")
        while True:
            tasks = await self.verified_pending(start_index)
            if not tasks:
                self.notify(
                    "done", f"序号 {start_index} 之后没有未完成的资源学习任务。"
                )
                return

            if not self.checkpoint.parallel_probe_done:
                self.checkpoint.concurrency = await self.probe_parallelism(tasks)
                self.checkpoint.parallel_probe_done = True
                self.store.save(self.checkpoint)
            workers = max(1, min(self.checkpoint.concurrency, len(tasks)))
            self.notify("info", f"本轮 {len(tasks)} 个未完成任务，worker={workers}")

            queue: asyncio.Queue[tuple[Course, Activity, ActivityStatus, int]] = asyncio.Queue()
            for item in tasks:
                queue.put_nowait(item)

            async def worker() -> None:
                while not queue.empty():
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    try:
                        await self.learn_one(*item)
                    except ActivityBlocked as exc:
                        self.notify("warning", str(exc))
                    finally:
                        queue.task_done()

            running = [asyncio.create_task(worker()) for _ in range(workers)]
            try:
                await asyncio.gather(*running)
            except ParallelLearningRejected:
                for task in running:
                    task.cancel()
                await asyncio.gather(*running, return_exceptions=True)
                if workers > 1:
                    self.checkpoint.concurrency = 1
                    self.store.save(self.checkpoint)
                    self.notify("warning", "运行中收到 UNTIMED，取消并行并从服务器状态串行续跑。")
                    continue
                if serial_conflict_recoveries >= 2:
                    raise ParallelLearningRejected(
                        "重建两次串行会话后服务器仍报告多终端；请关闭其他网页/设备上的学习页面。"
                    )
                serial_conflict_recoveries += 1
                self.notify(
                    "warning",
                    "服务器仍保留旧的活跃任务，正在重建串行学习会话并自动重试…",
                )
                if not await self.client.reset_api_session():
                    raise ParallelLearningRejected("自动重建学习会话失败，请重新登录。")
                await asyncio.sleep(self.settings.heartbeat_seconds * 2)
                continue

            if once:
                return
