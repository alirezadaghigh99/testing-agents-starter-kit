"""Pytest plugin that records, for each test, the path goals it reaches in solution.py.

Enabled with `-p testagent.pytest_paths`. Reads TESTAGENT_PATHS (output path) and
TESTAGENT_TARGET (path of solution.py). See testagent.paths for what a path goal is.
Each test's goals are written together with the number of calls it makes into solution.py.
"""

import json
import os

import pytest

from testagent.paths import PathTracer, decision_lines

_paths: dict[str, dict] = {}
_tracer: PathTracer | None = None


def pytest_configure(config):
    global _tracer
    target = os.environ.get("TESTAGENT_TARGET", "solution.py")
    try:
        _tracer = PathTracer(target, *decision_lines(open(target).read()))
    except (OSError, SyntaxError):
        _tracer = None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if _tracer is None:
        yield
        return
    _tracer.start()
    try:
        yield
    finally:
        goals = _tracer.stop()
        _paths[item.nodeid] = {"goals": goals, "calls": _tracer.calls}


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("TESTAGENT_PATHS")
    if path:
        with open(path, "w") as handle:
            json.dump(_paths, handle)
