from __future__ import annotations

from cloudriver_manager.auth import browser_channel_candidates


def test_default_edge_is_preferred_without_duplicates():
    channels = browser_channel_candidates("MSEdgeHTM")
    assert channels[0] == "msedge"
    assert channels[-1] is None
    assert len(channels) == len(set(channels))


def test_default_chrome_is_preferred():
    channels = browser_channel_candidates("ChromeHTML")
    assert channels[0] == "chrome"
    assert channels[-1] is None
