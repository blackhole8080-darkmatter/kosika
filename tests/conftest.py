"""Tests that need a server, a browser, a build or a subprocess.

The list lives here rather than in each file, so what leaves the fast lane
stays in one place that can be read at a glance:

    pytest -m "not slow"    the fast lane, a few minutes
    pytest -m slow          only these

Marking happens as tests are collected, so a file needs no `pytest` import of
its own and no test is edited to say it is slow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: Files measured at over ten seconds on this machine: they start a server, a
#: browser or a subprocess, build the Go engine, or drive the desktop app.
SLOW = {
    "test_app.py",
    "test_benchmark.py",
    "test_conformance.py",
    "test_corpus.py",
    "test_desktop_app.py",
    "test_diagnostics_cache.py",
    "test_engines.py",
    "test_example_agents.py",
    "test_hash_stability.py",
    "test_langflow_mcp.py",
    "test_sessions.py",
}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        if Path(str(item.fspath)).name in SLOW:
            item.add_marker(pytest.mark.slow)
