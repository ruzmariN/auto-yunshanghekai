from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


def analyze_har(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    endpoints: Counter[str] = Counter()
    heartbeat_points: list[float] = []
    heartbeat_intervals: list[float] = []
    previous_started: float | None = None
    relevant = []
    for entry in data["log"]["entries"]:
        request = entry["request"]
        split = urlsplit(request["url"])
        key = f"{request['method']} {split.scheme}://{split.netloc}{split.path}"
        endpoints[key] += 1
        if split.path.endswith("/learningBehavior/heartbeat"):
            query = dict(parse_qsl(split.query))
            heartbeat_points.append(float(query.get("timePoint", 0)))
            started = _iso_seconds(entry.get("startedDateTime", ""))
            if previous_started is not None:
                heartbeat_intervals.append(round(started - previous_started, 3))
            previous_started = started
        if any(
            marker in split.path
            for marker in (
                "/student/activity/",
                "/learningBehavior/heartbeat",
                "/student/resource/",
            )
        ):
            relevant.append(key)
    return {
        "file": str(path),
        "entries": len(data["log"]["entries"]),
        "endpoints": dict(endpoints),
        "relevant_endpoints": sorted(set(relevant)),
        "heartbeat_count": len(heartbeat_points),
        "heartbeat_points": heartbeat_points,
        "heartbeat_intervals": heartbeat_intervals,
    }


def _iso_seconds(value: str) -> float:
    if not value:
        return 0.0
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
