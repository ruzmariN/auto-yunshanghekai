from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloudriver_manager.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    state = {
        "cookies": [
            {
                "name": "JSESSIONID",
                "value": "test",
                "domain": "jxapp.open.ha.cn",
                "path": "/",
            }
        ],
        "origins": [],
        "referer": "https://jxapp.open.ha.cn/",
        "local_storage": {"heartBeatToken": "ABCDEFGH"},
    }
    session = tmp_path / "session.json"
    session.write_text(json.dumps(state), "utf-8")
    return Settings(
        data_dir=tmp_path,
        session_file=session,
        heartbeat_seconds=0.01,
        status_poll_seconds=0.01,
        max_workers=1,
        probe_parallelism=False,
    )
