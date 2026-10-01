from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, PositiveFloat, PositiveInt


class ApiPaths(BaseModel):
    online: str = "/user/isOnline"
    cas_login: str = "/user/casLogin"
    current_courses: str = "/student/activity/getCurrentCourseListByStudent"
    finished_courses: str = "/student/activity/getAllFinishCourseByStudent"
    course_content: str = "/student/activity/courseContent"
    activities: str = "/student/activity/getAllActivitys/{course_version_id}"
    display: str = "/student/activity/display"
    heartbeat: str = "/learningBehavior/heartbeat"
    finish: str = "/student/activity/finishActivity"


class Settings(BaseModel):
    model_config = ConfigDict(validate_default=True)

    front_url: HttpUrl = "https://jxapp.open.ha.cn"
    base_url: HttpUrl = "https://jxapp.open.ha.cn/jxpt-app"
    cas_url: HttpUrl = "https://authserver.open.ha.cn/authserver/"
    api: ApiPaths = Field(default_factory=ApiPaths)
    heartbeat_seconds: PositiveFloat = 10.0
    status_poll_seconds: PositiveFloat = 30.0
    request_timeout_seconds: PositiveFloat = 20.0
    # 已确认该平台会把主动双 activity 探测视为多终端，默认安全串行。
    max_workers: PositiveInt = 1
    probe_parallelism: bool = False
    headless_login: bool = False
    data_dir: Path = Path(".cloudriver")
    session_file: Path | None = None
    checkpoint_file: Path | None = None
    log_file: Path | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    )

    def model_post_init(self, __context: object) -> None:
        self.data_dir = self.data_dir.expanduser().resolve()
        self.session_file = (
            self.session_file or self.data_dir / "session.json"
        ).expanduser().resolve()
        self.checkpoint_file = (
            self.checkpoint_file or self.data_dir / "checkpoint.json"
        ).expanduser().resolve()
        self.log_file = (self.log_file or self.data_dir / "manager.log").expanduser().resolve()

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        if path is None:
            path = Path(".cloudriver/config.json")
        path = path.expanduser().resolve()
        if not path.exists():
            return cls(data_dir=path.parent)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.setdefault("data_dir", str(path.parent))
        return cls.model_validate(payload)

    def save(self, path: Path | None = None) -> Path:
        path = (path or self.data_dir / "config.json").expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json", exclude={"session_file", "checkpoint_file", "log_file"})
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
