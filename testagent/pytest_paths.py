"""Pytest plugin that counts calls into solution.py and records each test's path goals.

Enabled with `-p testagent.pytest_paths`. Reads TESTAGENT_PATHS (output path), TESTAGENT_TARGET
(path of solution.py) and TESTAGENT_GOALS ("1" to also record path goals, which is slower).
Calls are counted for the whole session: calls made while a test runs (setup, call or teardown)
belong to that test, and calls made anywhere else, such as at module level, are counted as outside.
"""

import json
import os
import sys

import pytest

from testagent.paths import PathTracer, decision_lines

OUTSIDE = "<outside>"
_target = ""
_tracer: PathTracer | None = None
_current = OUTSIDE
_calls: dict[str, int] = {}
_goals: dict[str, list[str]] = {}
_order: list[str] = []


def _profile(frame, event, arg):
    if event != "call" or frame.f_code.co_name == "<module>" or os.path.realpath(frame.f_code.co_filename) != _target:
        return
    caller = frame.f_back
    if caller is None or os.path.realpath(caller.f_code.co_filename) != _target:
        _calls[_current] = _calls.get(_current, 0) + 1


def pytest_configure(config):
    global _target, _tracer
    _target = os.path.realpath(os.environ.get("TESTAGENT_TARGET", "solution.py"))
    try:
        source = open(_target).read()
    except OSError:
        return
    if os.environ.get("TESTAGENT_GOALS") == "1":
        try:
            _tracer = PathTracer(_target, *decision_lines(source))
        except SyntaxError:
            _tracer = None
    sys.setprofile(_profile)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _current
    _current = item.nodeid
    _order.append(item.nodeid)
    yield
    _current = OUTSIDE


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if _tracer is None:
        yield
        return
    _tracer.start()
    try:
        yield
    finally:
        _goals[item.nodeid] = _tracer.stop()
        sys.setprofile(_profile)


def pytest_sessionfinish(session, exitstatus):
    sys.setprofile(None)
    path = os.environ.get("TESTAGENT_PATHS")
    if path:
        tests = {node: {"calls": _calls.get(node, 0), "goals": _goals.get(node, [])} for node in _order}
        with open(path, "w") as handle:
            json.dump({"tests": tests, "outside_calls": _calls.get(OUTSIDE, 0)}, handle)
