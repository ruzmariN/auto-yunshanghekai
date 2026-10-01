from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Course(FlexibleModel):
    course_version_id: str = Field(alias="courseVersionId")
    course_name: str = Field(default="", alias="courseName")
    finished: bool = False


class Activity(FlexibleModel):
    activity_id: str = Field(alias="activityId")
    course_version_id: str | None = Field(default=None, alias="courseVersionId")
    activity_name: str = Field(default="", alias="activityName")
    type: int = 0
    finish: int | None = None
    total_time: float | None = Field(default=None, alias="totalTime")
    length: float | None = None
    no_homework_record_and_over_time: int | None = Field(
        default=None, alias="noHomeworkRecordAndOverTime"
    )

    @property
    def is_catalog_finished(self) -> bool:
        return self.finish == 1

    @property
    def is_resource_activity(self) -> bool:
        return self.type in {2, 3, 7, 10}


class Unit(FlexibleModel):
    unit: str = ""
    activitys: list[Activity] = Field(default_factory=list)


class Resource(FlexibleModel):
    resource_id: str | None = Field(default=None, alias="resourceId")
    resource_center_id: str | None = Field(default=None, alias="resourceCenterId")
    name: str = ""
    mime_type: str = Field(default="", alias="mimeType")
    length: float = 0.0
    location: str | None = None
    source_code: int | str | None = Field(default=None, alias="sourceCode")


class ActivityStatus(FlexibleModel):
    type: int
    course_version_id: str = Field(alias="courseVersionId")
    activity_id: str = Field(alias="activityId")
    course_name: str = Field(default="", alias="courseName")
    time_point: float = Field(default=0.0, alias="timePoint")
    resource: Resource
    activity: dict[str, Any] = Field(default_factory=dict)
    resource_finished: int = Field(default=0, alias="resourceFinished")
    stu_resource_total_time: float = Field(default=0.0, alias="stuResourceTotalTime")
    homeworks: list[dict[str, Any]] = Field(default_factory=list)
    homework_num: int = Field(default=0, alias="homeworkNum")
    over_end_time: bool = Field(default=False, alias="overEndTime")

    @field_validator("time_point", "stu_resource_total_time", mode="before")
    @classmethod
    def none_to_zero(cls, value: Any) -> Any:
        return 0 if value is None else value

    @property
    def server_finished(self) -> bool:
        return self.resource_finished == 1

    @property
    def required_seconds(self) -> float:
        return max(float(self.resource.length or 0), 0.0)

    @property
    def progress_seconds(self) -> float:
        if self.type in {2, 7}:
            return max(float(self.time_point), float(self.stu_resource_total_time))
        return float(self.stu_resource_total_time)

    @property
    def normal_completion_reached(self) -> bool:
        return self.homework_num == 0 and self.progress_seconds >= self.required_seconds


class ApiEnvelope(FlexibleModel):
    error: bool = False
    code: str
    body: Any = None


class CheckpointItem(FlexibleModel):
    course_version_id: str
    activity_id: str
    token: str
    last_server_time_point: float = 0.0
    last_server_total_time: float = 0.0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Checkpoint(FlexibleModel):
    concurrency: int = 1
    parallel_probe_done: bool = False
    active: dict[str, CheckpointItem] = Field(default_factory=dict)
    completed: list[str] = Field(default_factory=list)

