"""Build the one final test suite per problem from the strategy in student/final.yaml.

Usage:
    python -m testagent.final --run my-final --workers 8
    python -m testagent.final --run my-final --merge-only

With `strategy: separate` the black-box agent runs, then the white-box agent, and the two suites are
merged. With `strategy: combined` the combined agent runs once. Either way the result goes to
runs/<run>/final/<problem>/, which is what `python -m testagent.evaluate --mode final` measures and
what we run on held-out problems. `--merge-only` merges black-box and white-box runs that already exist.
"""

import argparse
import json
import shutil
from pathlib import Path

import yaml

from testagent import REPO_ROOT
from testagent.problems import load_problems
from testagent.run import RUNS_DIR, STUDENT_DIR
from testagent.run import main as run_main

STRATEGIES = ("separate", "combined")
USAGE_KEYS = ("api_calls", "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd")


def load_strategy(student: Path) -> str:
    """The strategy named in final.yaml."""
    data = yaml.safe_load((student / "final.yaml").read_text()) or {}
    strategy = data.get("strategy")
    if strategy not in STRATEGIES:
        raise ValueError(f"final.yaml: strategy must be one of {STRATEGIES}, got {strategy!r}")
    return strategy


def merge_problem(run_dir: Path, problem_id: str, sources: list[str]) -> list[str]:
    """Copy the test files of the given modes into runs/<run>/final/<problem>/ and add up their usage."""
    target = run_dir / "final" / problem_id
    if target.exists():
        shutil.rmtree(target)
    (target / "tests").mkdir(parents=True)
    usage = {key: 0 for key in USAGE_KEYS}
    copied = []
    for mode in sources:
        source = run_dir / mode / problem_id
        for path in sorted((source / "tests").glob("test_*.py")) if (source / "tests").exists() else []:
            if path.name not in copied:
                shutil.copy(path, target / "tests" / path.name)
                copied.append(path.name)
        usage_path = source / "usage.json"
        if usage_path.exists():
            data = json.loads(usage_path.read_text())
            for key in USAGE_KEYS:
                usage[key] += data.get(key, 0)
    usage["sources"] = sources
    usage["test_files"] = copied
    (target / "usage.json").write_text(json.dumps(usage, indent=2))
    return copied


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--problems", default=str(REPO_ROOT / "data" / "dev"))
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--student", default=str(STUDENT_DIR))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--merge-only", action="store_true", help="only merge existing black-box and white-box runs")
    args = parser.parse_args(argv)

    student = Path(args.student).resolve()
    strategy = load_strategy(student)
    common = ["--run", args.run, "--problems", args.problems, "--student", str(student), "--workers", str(args.workers)]
    if args.only:
        common += ["--only", *args.only]
    if strategy == "separate":
        modes = ["blackbox", "whitebox"]
    else:
        modes = ["combined"]
    if not args.merge_only:
        for mode in modes:
            run_main(["--mode", mode, *common])
    run_dir = RUNS_DIR / args.run
    for problem in load_problems(Path(args.problems), args.only):
        files = merge_problem(run_dir, problem.id, modes)
        print(f"[final] {problem.id}: {files}", flush=True)
    print(f"Final suites in {run_dir / 'final'}. Measure them with: python -m testagent.evaluate --run {args.run} --mode final")


if __name__ == "__main__":
    main()
