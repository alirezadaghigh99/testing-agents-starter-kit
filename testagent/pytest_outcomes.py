"""Pytest plugin used by the evaluator for every run.

Enabled with `-p testagent.pytest_outcomes`. It
- shuffles test order (TESTAGENT_SEED, empty to keep file order),
- writes each test's outcome to TESTAGENT_OUTCOMES,
- stops any single test after TESTAGENT_TEST_TIMEOUT seconds, so one slow test fails on its own,
- stops test code (anything under the tests folder, at any time) from opening solution.py or reading
  the code object of anything defined in it, since such a test could detect changes without testing
  behaviour. Python's own import of solution.py is still allowed.
"""

import json
import os
import random
import signal
import sys

import pytest

_outcomes: dict[str, str] = {}
_target = ""
_tests_dir = ""


class SourceAccessError(Exception):
    """Raised when a test tries to read the code under test."""


def _from_test_code() -> bool:
    """True if test code, and not the import system acting for it, is asking."""
    frame = sys._getframe(2)
    while frame is not None:
        filename = frame.f_code.co_filename
        if filename.startswith("<frozen importlib"):
            return False
        if os.path.realpath(filename).startswith(_tests_dir):
            return True
        frame = frame.f_back
    return False


def _audit(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.path.realpath(os.fsdecode(args[0])) == _target and _from_test_code():
            raise SourceAccessError("tests may not open solution.py")
    elif event == "object.__getattr__" and len(args) >= 2 and args[1] == "__code__":
        if getattr(args[0], "__module__", None) == "solution" and _from_test_code():
            raise SourceAccessError("tests may not read the code object of solution.py")


def _timeout(signum, frame):
    raise TimeoutError(f"test took longer than {os.environ.get('TESTAGENT_TEST_TIMEOUT')} seconds")


def pytest_configure(config):
    global _target, _tests_dir
    _target = os.path.realpath(os.environ.get("TESTAGENT_TARGET", "solution.py"))
    _tests_dir = os.path.realpath(os.environ.get("TESTAGENT_TESTS", "tests")) + os.sep
    sys.addaudithook(_audit)


def pytest_collection_modifyitems(session, config, items):
    seed = os.environ.get("TESTAGENT_SEED", "")
    if seed:
        random.Random(int(seed)).shuffle(items)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    limit = int(os.environ.get("TESTAGENT_TEST_TIMEOUT", "0") or 0)
    if limit:
        signal.signal(signal.SIGALRM, _timeout)
        signal.alarm(limit)
    try:
        yield
    finally:
        if limit:
            signal.alarm(0)


def pytest_runtest_logreport(report):
    if report.failed:
        _outcomes[report.nodeid] = "failed"
    elif report.when == "call" and report.skipped:
        _outcomes.setdefault(report.nodeid, "skipped")
    elif report.when == "call" and report.passed:
        _outcomes.setdefault(report.nodeid, "passed")
    elif report.when == "setup" and report.skipped:
        _outcomes.setdefault(report.nodeid, "skipped")


def pytest_collectreport(report):
    if report.failed:
        _outcomes[f"<collection>::{report.nodeid}"] = "error"


def pytest_sessionfinish(session, exitstatus):
    path = os.environ.get("TESTAGENT_OUTCOMES")
    if path:
        with open(path, "w") as handle:
            json.dump(_outcomes, handle)
