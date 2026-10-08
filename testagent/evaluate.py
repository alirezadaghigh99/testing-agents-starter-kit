"""Score the tests an agent produced, against the reference solutions.

Usage:
    python -m testagent.evaluate --run my-run --mode blackbox
    python -m testagent.evaluate --run my-run --mode whitebox
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from testagent import REPO_ROOT
from testagent.mutation import generate_mutants
from testagent.problems import Problem, load_problems
from testagent.run import load_settings, mode_limits
from testagent.workspace import TEST_FILES

RUNS_DIR = REPO_ROOT / "runs"
PYTEST_TIMEOUT = 120
MUTANT_TIMEOUT = 120

# Tests must treat solution.py as a black box. A file that imports one of these modules or uses one
# of these names can read the source under test (and so "kill" every variant by comparing text),
# so the whole file is rejected before scoring.
FORBIDDEN_MODULES = {"inspect", "os", "io", "pathlib", "importlib", "linecache", "dis", "subprocess",
                     "shutil", "glob", "marshal", "ast", "tokenize", "pkgutil", "runpy"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "globals", "vars", "__import__", "breakpoint"}
FORBIDDEN_ATTRS = {"__code__", "__file__", "__globals__", "__dict__", "__loader__", "__spec__", "modules",
                   "getsource", "getsourcelines", "read_text", "read_bytes"}


@dataclass
class SuiteScore:
    """Scores for one test suite on one problem."""

    tests_total: int = 0
    tests_valid: int = 0
    wrong_oracle: int = 0
    flaky: int = 0
    collection_errors: int = 0
    line_coverage: float = 0.0
    branch_coverage: float = 0.0
    mutants_total: int = 0
    mutants_killed: int = 0
    mutation_score: float = 0.0
    hard_mutants_total: int = 0
    hard_mutants_killed: int = 0
    hard_mutation_score: float = 0.0
    path_coverage: float | None = 0.0
    calls_used: int = 0
    tests_over_budget: int = 0
    paths: list = field(default_factory=list)
    bugs_total: int = 0
    bugs_killed: int = 0
    bug_kill_rate: float = 0.0
    oracle_precision: float = 0.0
    smells: dict = field(default_factory=dict)
    rejected_files: list = field(default_factory=list)
    surviving_mutants: list = field(default_factory=list)
    surviving_bugs: list = field(default_factory=list)


def _sandbox(problem: Problem, test_files: list[Path]) -> Path:
    root = Path(tempfile.mkdtemp(prefix=f"testagent-eval-{problem.id}-"))
    (root / "tests").mkdir()
    (root / "solution.py").write_text(problem.reference_solution)
    for path in test_files:
        shutil.copy(path, root / "tests" / path.name)
    return root


def run_pytest(root: Path, node_ids: list[str] | None = None, *, seed: str = "", timeout: int = PYTEST_TIMEOUT,
               extra_args: list[str] | None = None, stop_first: bool = False,
               extra_env: dict[str, str] | None = None) -> dict[str, str] | None:
    """Run pytest in `root` and return {nodeid: outcome}, or None on timeout."""
    outcomes_path = root / ".outcomes.json"
    outcomes_path.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "testagent.pytest_outcomes",
           "--continue-on-collection-errors"]
    if stop_first:
        cmd.append("-x")
    cmd += extra_args or []
    cmd += node_ids if node_ids else ["tests"]
    env = os.environ | {
        "TESTAGENT_OUTCOMES": str(outcomes_path),
        "TESTAGENT_SEED": seed,
        "PYTHONPATH": os.pathsep.join([str(root), str(REPO_ROOT)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "COVERAGE_FILE": str(root / ".coverage"),
    } | (extra_env or {})
    try:
        subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if not outcomes_path.exists():
        return {}
    return json.loads(outcomes_path.read_text())


def classify(root: Path, reruns: int) -> tuple[list[str], int, int, int]:
    """Run the suite several times in random order on the reference.

    Returns (valid test ids, wrong-oracle count, flaky count, collection errors).
    """
    runs = [run_pytest(root, seed=str(1000 + i)) or {} for i in range(reruns)]
    collection_errors = sum(1 for k in runs[0] if k.startswith("<collection>"))
    node_ids = sorted({k for run in runs for k in run if not k.startswith("<collection>")})
    valid, wrong, flaky = [], 0, 0
    for node_id in node_ids:
        results = {run.get(node_id, "missing") for run in runs}
        if results == {"passed"}:
            valid.append(node_id)
        elif "passed" in results:
            flaky += 1
        elif results != {"skipped"}:
            wrong += 1
    return valid, wrong, flaky, collection_errors


def coverage(root: Path, node_ids: list[str]) -> tuple[float, float]:
    """Line and branch coverage of solution.py achieved by the given tests."""
    if not node_ids:
        return 0.0, 0.0
    report = root / ".coverage.json"
    run_pytest(root, node_ids, extra_args=["--cov=solution", "--cov-branch", f"--cov-report=json:{report}"])
    if not report.exists():
        return 0.0, 0.0
    data = json.loads(report.read_text())
    summary = next(iter(data["files"].values()))["summary"] if data.get("files") else {}
    statements = summary.get("num_statements", 0)
    branches = summary.get("num_branches", 0)
    line = summary.get("covered_lines", 0) / statements if statements else 1.0
    branch = summary.get("covered_branches", 0) / branches if branches else 1.0
    return round(line, 4), round(branch, 4)


def scored_mutants(problem: Problem, max_mutants: int = 0) -> list:
    """Mutants used for scoring: all generated mutants minus those known to be equivalent."""
    equivalent = set(problem.equivalent_mutants)
    mutants = [m for m in generate_mutants(problem.reference_solution) if m.id not in equivalent]
    return mutants[:max_mutants] if max_mutants else mutants


def trace_tests(root: Path, node_ids: list[str] | None = None) -> dict[str, dict]:
    """Path goals and number of calls into solution.py for each test, in file order."""
    output = root / ".paths.json"
    output.unlink(missing_ok=True)
    run_pytest(root, node_ids, extra_args=["-p", "testagent.pytest_paths"],
               extra_env={"TESTAGENT_PATHS": str(output), "TESTAGENT_TARGET": str(root / "solution.py")})
    return json.loads(output.read_text()) if output.exists() else {}


def suite_paths(root: Path, node_ids: list[str]) -> set[str]:
    """Path goals in solution.py reached by the given tests."""
    if not node_ids:
        return set()
    return {goal for entry in trace_tests(root, node_ids).values() for goal in entry["goals"]}


def within_budget(root: Path, valid: list[str], max_calls: int) -> tuple[list[str], int]:
    """Valid tests that fit the call budget.

    Every test, valid or not, spends its calls into solution.py in file order. Tests after the
    budget runs out are not measured.
    """
    if not max_calls:
        return valid, sum(entry["calls"] for entry in trace_tests(root, valid).values()) if valid else 0
    allowed, used, wanted = [], 0, set(valid)
    for node_id, entry in trace_tests(root).items():
        if used + entry["calls"] > max_calls:
            break
        used += entry["calls"]
        if node_id in wanted:
            allowed.append(node_id)
    return allowed, used


def variant_timeout(root: Path, node_ids: list[str]) -> int:
    """Timeout for one mutant or bug run: ten times the suite's time on the reference, between 10 and MUTANT_TIMEOUT seconds."""
    start = time.monotonic()
    run_pytest(root, node_ids, timeout=MUTANT_TIMEOUT)
    return int(min(MUTANT_TIMEOUT, max(10, 10 * (time.monotonic() - start))))


def kills(root: Path, problem: Problem, node_ids: list[str], variants: list[tuple[str, str]],
          timeout: int = MUTANT_TIMEOUT) -> list[str]:
    """Run the valid tests against each (label, source) variant and return the labels that survive."""
    if not node_ids:
        return [label for label, _ in variants]
    surviving = []
    for label, source in variants:
        (root / "solution.py").write_text(source)
        outcomes = run_pytest(root, node_ids, timeout=timeout, stop_first=True)
        if not (outcomes is None or not outcomes or any(v != "passed" for v in outcomes.values())):
            surviving.append(label)
    (root / "solution.py").write_text(problem.reference_solution)
    return surviving


def oracle_precision(score: "SuiteScore") -> float:
    """Share of the suite's tests that were valid on the reference."""
    judged = score.tests_valid + score.wrong_oracle + score.flaky
    return round(score.tests_valid / judged, 4) if judged else 0.0


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.With):
        return any("raises" in ast.unparse(item.context_expr) for item in node.items)
    if isinstance(node, ast.Call):
        name = ast.unparse(node.func)
        return "assert" in name or name.endswith("fail")
    return False


def source_access(path: Path) -> str:
    """Why a test file may read the code under test, or '' if it looks clean."""
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            names = set()
        hit = names & FORBIDDEN_MODULES
        if hit:
            return f"imports {sorted(hit)[0]}"
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"uses {node.id}"
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
            return f"uses .{node.attr}"
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "solution.py" in node.value:
            return "mentions solution.py"
    return ""


def smells(test_files: list[Path]) -> dict:
    """Count simple test smells: no assertion, constant assertion, duplicates, broad raises."""
    counts = {"no_assertion": 0, "constant_assertion": 0, "duplicate_test": 0, "broad_exception": 0}
    bodies = set()
    for path in test_files:
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
                continue
            inner = list(ast.walk(node))
            if not any(_is_assertion(n) for n in inner):
                counts["no_assertion"] += 1
            for n in inner:
                if isinstance(n, ast.Assert) and isinstance(n.test, ast.Constant):
                    counts["constant_assertion"] += 1
                if isinstance(n, ast.With) and any(
                    ast.unparse(item.context_expr).replace(" ", "").endswith("raises(Exception)")
                    for item in n.items
                ):
                    counts["broad_exception"] += 1
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if body in bodies:
                counts["duplicate_test"] += 1
            bodies.add(body)
    return counts


def score_suite(problem: Problem, test_files: list[Path], *, reruns: int, max_mutants: int,
                max_calls: int = 0) -> SuiteScore:
    """Measure a set of test files against the reference, its mutants and its known bugs.

    Only valid tests within the call budget (max_calls calls into solution.py, 0 for no limit) count.
    """
    rejected = {path: source_access(path) for path in test_files}
    rejected = {path: why for path, why in rejected.items() if why}
    test_files = [path for path in test_files if path not in rejected]
    score = SuiteScore(smells=smells(test_files), rejected_files=[f"{p.name}: {why}" for p, why in rejected.items()])
    mutants = scored_mutants(problem, max_mutants)
    score.mutants_total, score.bugs_total = len(mutants), len(problem.bugs)
    if test_files:
        root = _sandbox(problem, test_files)
        try:
            valid, score.wrong_oracle, score.flaky, score.collection_errors = classify(root, reruns)
            score.tests_valid = len(valid)
            score.tests_total = len(valid) + score.wrong_oracle + score.flaky
            valid, score.calls_used = within_budget(root, valid, max_calls)
            score.tests_over_budget = score.tests_valid - len(valid)
            score.line_coverage, score.branch_coverage = coverage(root, valid)
            score.paths = sorted(suite_paths(root, valid))
            timeout = variant_timeout(root, valid) if valid else MUTANT_TIMEOUT
            mutant_variants = [(f"{m.id} line {m.line} {m.operator}", m.source) for m in mutants]
            score.surviving_mutants = kills(root, problem, valid, mutant_variants, timeout)
            bug_variants = [(f"{b['id']}: {b.get('description', '')}", b["source"]) for b in problem.bugs]
            score.surviving_bugs = kills(root, problem, valid, bug_variants, timeout)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    else:
        score.surviving_mutants = [m.id for m in mutants]
        score.surviving_bugs = [b["id"] for b in problem.bugs]
    score.mutants_killed = score.mutants_total - len(score.surviving_mutants)
    hard = {m.id for m in mutants} & set(problem.hard_mutants)
    surviving_ids = {label.split()[0] for label in score.surviving_mutants}
    score.hard_mutants_total = len(hard)
    score.hard_mutants_killed = len(hard - surviving_ids)
    score.hard_mutation_score = round(score.hard_mutants_killed / len(hard), 4) if hard else 0.0

    score.bugs_killed = score.bugs_total - len(score.surviving_bugs)
    score.mutation_score = round(score.mutants_killed / score.mutants_total, 4) if score.mutants_total else 0.0
    score.bug_kill_rate = round(score.bugs_killed / score.bugs_total, 4) if score.bugs_total else 0.0
    pool = set(problem.reference_paths) | set(score.paths)
    score.path_coverage = round(len(score.paths) / len(pool), 4) if pool else None
    score.oracle_precision = oracle_precision(score)
    return score


def call_budget(mode: str) -> int:
    """The max_calls limit for a mode from settings.yaml, 0 if there is none."""
    return int(mode_limits(load_settings(), mode).get("max_calls", 0))


def evaluate_problem(problem: Problem, result_dir: Path, mode: str, *, reruns: int = 3, max_mutants: int = 0) -> dict:
    """Score one problem's output from a run."""
    tests_dir = result_dir / "tests"
    files = sorted(tests_dir.glob("*.py")) if tests_dir.exists() else []
    usage_path = result_dir / "usage.json"
    usage = json.loads(usage_path.read_text()) if usage_path.exists() else {}
    report = {"problem_id": problem.id, "mode": mode, "usage": usage}
    report["suite"] = asdict(score_suite(problem, files, reruns=reruns, max_mutants=max_mutants,
                                         max_calls=call_budget(mode)))
    if mode in ("whitebox", "final"):
        blackbox_only = [f for f in files if f.name == TEST_FILES["blackbox"]]
        if blackbox_only:
            report["blackbox_baseline"] = asdict(score_suite(problem, blackbox_only, reruns=reruns,
                                                             max_mutants=max_mutants, max_calls=call_budget("blackbox")))
    return report


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def summarize(reports: list[dict]) -> dict:
    """Each metric over all problems, reported separately."""
    suites = [r["suite"] for r in reports]
    judged = sum(s["tests_valid"] + s["wrong_oracle"] + s["flaky"] for s in suites)
    with_bugs = [s for s in suites if s["bugs_total"]]
    with_hard = [s for s in suites if s["hard_mutants_total"]]
    summary = {
        "problems": len(reports),
        "bug_kill_rate": _mean([s["bug_kill_rate"] for s in with_bugs]),
        "bugs_killed": f"{sum(s['bugs_killed'] for s in suites)}/{sum(s['bugs_total'] for s in suites)}",
        "oracle_precision": round(sum(s["tests_valid"] for s in suites) / judged, 4) if judged else 0.0,
        "wrong_oracle_tests": sum(s["wrong_oracle"] for s in suites),
        "flaky_tests": sum(s["flaky"] for s in suites),
        "rejected_files": sum(len(s["rejected_files"]) for s in suites),
        "valid_tests": sum(s["tests_valid"] for s in suites),
        "problems_with_no_valid_test": sum(1 for s in suites if s["tests_valid"] == 0),
        "hard_mutation_score": _mean([s["hard_mutation_score"] for s in with_hard]),
        "hard_mutants_killed": f"{sum(s['hard_mutants_killed'] for s in suites)}/{sum(s['hard_mutants_total'] for s in suites)}",
        "mutation_score": _mean([s["mutation_score"] for s in suites]),
        "branch_coverage": _mean([s["branch_coverage"] for s in suites]),
        "path_coverage": _mean([s["path_coverage"] for s in suites if s["path_coverage"] is not None]),
        "path_goals_per_problem": _mean([len(s["paths"]) for s in suites]),
        "tests_over_budget": sum(s["tests_over_budget"] for s in suites),
        "smells": {k: sum(s["smells"].get(k, 0) for s in suites) for k in suites[0]["smells"]} if suites else {},
        "total_tokens": sum(r["usage"].get("total_tokens", 0) for r in reports),
        "cost_usd": round(sum(r["usage"].get("cost_usd", 0.0) for r in reports), 6),
    }
    baselines = [r["blackbox_baseline"] for r in reports if "blackbox_baseline" in r]
    if baselines:
        summary["blackbox_tests_alone"] = {
            "bugs_killed": f"{sum(b['bugs_killed'] for b in baselines)}/{sum(b['bugs_total'] for b in baselines)}",
            "hard_mutants_killed": f"{sum(b['hard_mutants_killed'] for b in baselines)}/{sum(b['hard_mutants_total'] for b in baselines)}",
            "mutation_score": _mean([b["mutation_score"] for b in baselines]),
            "branch_coverage": _mean([b["branch_coverage"] for b in baselines]),
            "path_coverage": _mean([b["path_coverage"] for b in baselines if b["path_coverage"] is not None]),
        }
    return summary


def print_table(reports: list[dict], summary: dict) -> None:
    header = (f"{'problem':<12} {'valid':>5} {'wrong':>5} {'bugs':>7} {'hard':>7} {'paths':>5} {'mut':>5}"
              f" {'branch':>6} {'tokens':>8} {'cost':>8}")
    print(header)
    print("-" * len(header))
    for r in reports:
        s, u = r["suite"], r["usage"]
        print(
            f"{r['problem_id']:<12} {s['tests_valid']:>5} {s['wrong_oracle']:>5}"
            f" {str(s['bugs_killed']) + '/' + str(s['bugs_total']):>7}"
            f" {str(s['hard_mutants_killed']) + '/' + str(s['hard_mutants_total']):>7}"
            f" {s['path_coverage'] if s['path_coverage'] is not None else float('nan'):>5.2f}"
            f" {s['mutation_score']:>5.2f} {s['branch_coverage']:>6.2f}"
            f" {u.get('total_tokens', 0):>8} {u.get('cost_usd', 0.0):>8.4f}"
        )
    print("-" * len(header))
    print(json.dumps(summary, indent=2))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--mode", choices=["blackbox", "whitebox", "combined", "final"], required=True)
    parser.add_argument("--problems", default=str(REPO_ROOT / "data" / "dev"))
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--reruns", type=int, default=3, help="random-order reruns used to detect flaky tests")
    parser.add_argument("--max-mutants", type=int, default=0, help="cap on mutants per problem, 0 for all")
    parser.add_argument("--workers", type=int, default=4, help="problems to evaluate in parallel")
    args = parser.parse_args(argv)

    mode_dir = RUNS_DIR / args.run / args.mode
    todo = []
    for problem in load_problems(Path(args.problems), args.only):
        if (mode_dir / problem.id).exists():
            todo.append(problem)
        else:
            print(f"skipping {problem.id}: no results in {mode_dir / problem.id}")

    def evaluate_one(problem: Problem) -> dict:
        report = evaluate_problem(problem, mode_dir / problem.id, args.mode, reruns=args.reruns, max_mutants=args.max_mutants)
        print(f"evaluated {problem.id}", flush=True)
        return report

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        reports = list(pool.map(evaluate_one, todo))
    if not reports:
        print("Nothing to evaluate.")
        return
    summary = summarize(reports)
    (mode_dir / "evaluation.json").write_text(json.dumps({"summary": summary, "problems": reports}, indent=2))
    print_table(reports, summary)
    print(f"Saved to {mode_dir / 'evaluation.json'}")


if __name__ == "__main__":
    main()
