"""Tests for the harness itself. They use a scripted model, so no API key is needed."""

import json
import shutil
from pathlib import Path

import pytest
from minisweagent.models.test_models import DeterministicModel

from testagent import REPO_ROOT
from testagent.evaluate import evaluate_problem
from testagent.mutation import generate_mutants
from testagent.problems import load_problem, load_problems
from testagent.run import load_settings, mode_limits, run_problem
from testagent.workspace import create_workspace

DEV = REPO_ROOT / "data" / "dev"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "example-peaks.json"

GOOD_TESTS = '''from solution import Solution


def test_example_one():
    assert Solution().countPeaks([1, 3, 2]) == 1


def test_example_two():
    assert Solution().countPeaks([1, 2, 3]) == 0


def test_single_element():
    assert Solution().countPeaks([5]) == 0


def test_plateau_is_not_a_peak():
    assert Solution().countPeaks([1, 3, 3, 1]) == 0


def test_two_peaks_last_index_checked():
    assert Solution().countPeaks([1, 3, 1, 3, 1]) == 2
'''

WRONG_TESTS = '''from solution import Solution


def test_wrong_expectation():
    assert Solution().countPeaks([1, 2, 3]) == 1
'''


def scripted(*commands: str, tokens: int = 100) -> DeterministicModel:
    """A model that issues the given bash commands in order and reports fake usage."""
    outputs = [
        {
            "role": "assistant",
            "content": f"step {i}",
            "extra": {
                "actions": [{"command": command}],
                "cost": 0.001,
                "response": {"usage": {"prompt_tokens": tokens, "completion_tokens": tokens // 10}},
            },
        }
        for i, command in enumerate(commands)
    ]
    return DeterministicModel(outputs=outputs)


def write_file(path: str, text: str) -> str:
    return f"cat > {path} <<'PYEOF'\n{text}PYEOF"


@pytest.fixture
def problem():
    return load_problem(FIXTURE)


def test_dev_problems_load():
    problems = load_problems(DEV)
    assert len(problems) >= 5
    assert all(p.reference_solution and p.signature.startswith("def ") for p in problems)


def test_blackbox_workspace_hides_reference(problem):
    workspace = create_workspace(problem, "blackbox")
    stub = (workspace / "solution.py").read_text()
    assert "NotImplementedError" in stub
    assert problem.reference_solution.strip() not in stub


def test_mutants_are_distinct_and_compile():
    for problem in load_problems(DEV):
        mutants = generate_mutants(problem.reference_solution)
        assert mutants
        sources = [m.source for m in mutants]
        assert len(sources) == len(set(sources))
        for source in sources:
            compile(source, "<mutant>", "exec")


def test_blackbox_run_and_evaluate(problem, tmp_path):
    model = scripted(
        write_file("tests/test_blackbox.py", GOOD_TESTS),
        "check_tests",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    )
    usage = run_problem(problem, "blackbox", tmp_path, model=model)
    assert usage["exit_status"] == "Submitted"
    assert usage["test_files"] == ["test_blackbox.py"]
    assert usage["total_tokens"] == 330
    assert json.loads((tmp_path / "trajectory.json").read_text())["info"]["model_stats"]["total_tokens"] == 330

    report = evaluate_problem(problem, tmp_path, "blackbox", reruns=2)
    suite = report["suite"]
    assert suite["tests_valid"] == 5
    assert suite["wrong_oracle"] == 0
    assert suite["branch_coverage"] > 0.5
    assert 0 < suite["mutation_score"] <= 1


def test_wrong_oracle_is_detected(problem, tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_blackbox.py").write_text(WRONG_TESTS)
    suite = evaluate_problem(problem, tmp_path, "blackbox", reruns=1)["suite"]
    assert suite["wrong_oracle"] == 1
    assert suite["tests_valid"] == 0
    assert suite["mutation_score"] == 0


def test_whitebox_reports_blackbox_baseline(problem, tmp_path):
    blackbox_tests = tmp_path / "bb" / "test_blackbox.py"
    blackbox_tests.parent.mkdir()
    blackbox_tests.write_text(GOOD_TESTS)
    extra = GOOD_TESTS.replace("def test_", "def test_wb_")
    model = scripted(
        "ls tests",
        write_file("tests/test_whitebox.py", extra),
        "coverage_report",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    )
    out = tmp_path / "wb"
    usage = run_problem(problem, "whitebox", out, model=model, blackbox_tests=blackbox_tests)
    assert sorted(usage["test_files"]) == ["test_blackbox.py", "test_whitebox.py"]
    report = evaluate_problem(problem, out, "whitebox", reruns=1)
    assert report["suite"]["tests_valid"] == 10
    assert report["blackbox_baseline"]["tests_valid"] == 5


def test_token_limit_stops_the_agent(problem, tmp_path):
    settings = load_settings()
    settings["limits"]["token_limit"] = 150
    model = scripted("ls", "ls", "ls", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", tokens=100)
    usage = run_problem(problem, "blackbox", tmp_path, model=model, settings=settings)
    assert usage["exit_status"] == "TokenLimitExceeded"
    assert usage["api_calls"] == 2


def test_bugs_and_equivalent_mutants_are_scored(problem, tmp_path):
    reference = problem.reference_solution
    problem.bugs = [
        {"id": "bug_1", "description": "plateaus count as peaks", "source": reference.replace("nums[i] > nums[i + 1]", "nums[i] >= nums[i + 1]")},
        {"id": "bug_2", "description": "skips the last candidate", "source": reference.replace("len(nums) - 1", "len(nums) - 2")},
    ]
    mutants = generate_mutants(reference)
    problem.equivalent_mutants = [mutants[0].id]
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_blackbox.py").write_text(GOOD_TESTS)
    suite = evaluate_problem(problem, tmp_path, "blackbox", reruns=1)["suite"]
    assert suite["mutants_total"] == len(mutants) - 1
    assert suite["bugs_total"] == 2
    assert suite["bugs_killed"] == 2
    assert suite["oracle_precision"] == 1.0
    assert suite["paths"]


def test_wrong_oracles_lower_oracle_precision(problem, tmp_path):
    mixed = tmp_path / "mixed"
    (mixed / "tests").mkdir(parents=True)
    (mixed / "tests" / "test_blackbox.py").write_text(GOOD_TESTS + WRONG_TESTS.split("\n", 1)[1])
    suite = evaluate_problem(problem, mixed, "blackbox", reruns=1)["suite"]
    assert suite["wrong_oracle"] == 1
    assert suite["oracle_precision"] == round(5 / 6, 4)


def test_blackbox_spec_has_no_examples(problem):
    blackbox = (create_workspace(problem, "blackbox") / "SPEC.md").read_text()
    whitebox = (create_workspace(problem, "whitebox") / "SPEC.md").read_text()
    assert "Example 1" not in blackbox and "Input:" not in blackbox
    assert "Constraints" in blackbox
    assert "Example 1" in whitebox


def test_hard_mutants_are_scored_separately(problem, tmp_path):
    mutants = generate_mutants(problem.reference_solution)
    problem.hard_mutants = [m.id for m in mutants[:3]]
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_blackbox.py").write_text(GOOD_TESTS)
    suite = evaluate_problem(problem, tmp_path, "blackbox", reruns=1)["suite"]
    assert suite["hard_mutants_total"] == 3
    assert 0 <= suite["hard_mutation_score"] <= 1


def test_whitebox_has_its_own_limits():
    settings = load_settings()
    assert mode_limits(settings, "whitebox")["token_limit"] > mode_limits(settings, "blackbox")["token_limit"]


def test_secrets_are_hidden_from_agent_commands(problem, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    model = scripted(
        "echo key=[$OPENROUTER_API_KEY]",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
    )
    run_problem(problem, "blackbox", tmp_path, model=model)
    trajectory = (tmp_path / "trajectory.json").read_text()
    assert "key=[]" in trajectory
    assert "sk-secret" not in trajectory


def test_submission_checker(tmp_path):
    from testagent.submission import check

    shutil.copytree(REPO_ROOT / "student", tmp_path / "student")
    assert check(tmp_path / "student") == []
    bad = tmp_path / "student" / "tools" / "shared" / "ask_llm"
    bad.write_text("#!/usr/bin/env python3\nimport requests\nprint('help')\n")
    bad.chmod(0o755)
    errors = check(tmp_path / "student")
    assert any("ask_llm" in e and "import" in e for e in errors)


def test_call_budget_limits_scored_tests(problem, tmp_path):
    from testagent.evaluate import score_suite

    path = tmp_path / "test_blackbox.py"
    path.write_text(GOOD_TESTS)
    suite = score_suite(problem, [path], reruns=1, max_mutants=0, max_calls=3)
    assert suite.tests_valid == 5
    assert suite.calls_used == 3
    assert suite.tests_over_budget == 2


def test_final_merges_blackbox_and_whitebox(tmp_path):
    from testagent.final import merge_problem

    for mode, files in (("blackbox", ["test_blackbox.py"]), ("whitebox", ["test_blackbox.py", "test_whitebox.py"])):
        folder = tmp_path / mode / "p1" / "tests"
        folder.mkdir(parents=True)
        for name in files:
            (folder / name).write_text("def test_x():\n    assert True\n")
        (tmp_path / mode / "p1" / "usage.json").write_text(json.dumps({"total_tokens": 10, "cost_usd": 0.5}))
    copied = merge_problem(tmp_path, "p1", ["blackbox", "whitebox"])
    assert copied == ["test_blackbox.py", "test_whitebox.py"]
    usage = json.loads((tmp_path / "final" / "p1" / "usage.json").read_text())
    assert usage["total_tokens"] == 20 and usage["cost_usd"] == 1.0


def test_combined_workspace_has_code_and_full_spec(problem):
    workspace = create_workspace(problem, "combined")
    assert (workspace / "solution.py").read_text() == problem.reference_solution
    assert "Example 1" in (workspace / "SPEC.md").read_text()
    assert not list((workspace / "tests").iterdir())


def test_wrong_tests_spend_the_call_budget(problem, tmp_path):
    from testagent.evaluate import score_suite

    path = tmp_path / "test_blackbox.py"
    path.write_text(WRONG_TESTS + GOOD_TESTS.split("\n", 1)[1])
    suite = score_suite(problem, [path], reruns=1, max_mutants=0, max_calls=3)
    assert suite.wrong_oracle == 1
    assert suite.calls_used == 3
    assert suite.tests_valid - suite.tests_over_budget == 2


def test_checker_requires_a_valid_final_strategy(tmp_path):
    from testagent.submission import check

    shutil.copytree(REPO_ROOT / "student", tmp_path / "student")
    (tmp_path / "student" / "final.yaml").write_text("strategy: both\n")
    assert any("strategy must be" in e for e in check(tmp_path / "student"))


MODULE_LEVEL_CALLS = """from solution import Solution

RESULTS = [Solution().countPeaks([i, i + 2, i]) for i in range(200)]


def test_all():
    assert all(r == 1 for r in RESULTS)
"""

FIXTURE_CALLS = """import pytest
from solution import Solution


@pytest.fixture
def results():
    return [Solution().countPeaks([i, i + 2, i]) for i in range(200)]


def test_all(results):
    assert all(r == 1 for r in results)
"""

BYTECODE_HASH = """import hashlib
from solution import Solution


def test_code_unchanged():
    code = getattr(Solution.countPeaks, '__co' + 'de__')
    assert hashlib.sha1(code.co_code).hexdigest() == hashlib.sha1(code.co_code).hexdigest()
"""

SLOW_AND_FAST = """import time
from solution import Solution


def test_slow():
    time.sleep(5)
    assert Solution().countPeaks([1, 3, 2]) == 1


def test_fast():
    assert Solution().countPeaks([1, 3, 1, 3, 1]) == 2
"""


def _suite(problem, tmp_path, text, max_calls=25):
    from testagent.evaluate import score_suite

    path = tmp_path / "test_blackbox.py"
    path.write_text(text)
    return score_suite(problem, [path], reruns=1, max_calls=max_calls, max_mutants=0)


def test_module_level_calls_spend_the_budget(problem, tmp_path):
    suite = _suite(problem, tmp_path, MODULE_LEVEL_CALLS)
    assert suite.calls_outside_tests == 200
    assert suite.bugs_killed == 0 and suite.mutants_killed == 0


def test_fixture_calls_count_for_their_test(problem, tmp_path):
    suite = _suite(problem, tmp_path, FIXTURE_CALLS)
    assert suite.tests_over_budget == 1
    assert suite.mutants_killed == 0


def test_reading_the_code_object_is_blocked(problem, tmp_path):
    suite = _suite(problem, tmp_path, BYTECODE_HASH)
    assert suite.tests_valid == 0 and suite.wrong_oracle == 1
    assert suite.mutants_killed == 0


def test_a_slow_test_fails_alone(problem, tmp_path, monkeypatch):
    import testagent.evaluate as evaluate

    monkeypatch.setattr(evaluate, "TEST_TIMEOUT", 1)
    suite = _suite(problem, tmp_path, SLOW_AND_FAST)
    assert suite.tests_valid == 1 and suite.wrong_oracle == 1


def test_inner_loop_that_does_not_run_is_recorded(tmp_path):
    from testagent.paths import PathTracer, decision_lines

    source = "def f(rows):\n    total = 0\n    for row in rows:\n        for x in row:\n            total += x\n    return total\n"
    path = tmp_path / "loops.py"
    path.write_text(source)
    namespace = {}
    exec(compile(source, str(path), "exec"), namespace)
    tracer = PathTracer(str(path), *decision_lines(source))
    tracer.start()
    namespace["f"]([[], [], []])
    goals = tracer.stop()
    assert "loop 4 0" in goals and "loop 3 many" in goals
