"""Pytest plugin that shuffles test order and writes per-test outcomes to JSON.

Enabled with `-p testagent.pytest_outcomes`. Reads TESTAGENT_OUTCOMES (output path)
and TESTAGENT_SEED (shuffle seed, empty to keep the original order).
"""

import json
import os
import random

_outcomes: dict[str, str] = {}


def pytest_collection_modifyitems(session, config, items):
    seed = os.environ.get("TESTAGENT_SEED", "")
    if seed:
        random.Random(int(seed)).shuffle(items)


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
