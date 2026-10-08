"""Run the agent on a set of problems in black-box or white-box mode.

Usage:
    python -m testagent.run --mode blackbox --run my-run
    python -m testagent.run --mode whitebox --run my-run --workers 8
"""

import argparse
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models import get_model

from testagent import REPO_ROOT, toolbox
from testagent.agent import TestingAgent
from testagent.problems import Problem, load_problems
from testagent.workspace import TEST_FILES, collect_tests, create_workspace, spec_markdown

SETTINGS_PATH = REPO_ROOT / "settings.yaml"
STUDENT_DIR = REPO_ROOT / "student"
PROMPTS_DIR = STUDENT_DIR / "prompts"
RUNS_DIR = REPO_ROOT / "runs"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """Read the fixed course settings."""
    return yaml.safe_load(Path(path).read_text())


def mode_limits(settings: dict, mode: str) -> dict:
    """The limits for a mode: the shared limits with that mode's overrides applied."""
    shared = {k: v for k, v in settings["limits"].items() if not isinstance(v, dict)}
    return shared | settings["limits"].get(mode, {})


def load_prompts(mode: str, prompts_dir: Path = PROMPTS_DIR) -> dict:
    """Read the student's system and instance templates for a mode."""
    data = yaml.safe_load((prompts_dir / f"{mode}.yaml").read_text())
    return {"system_template": data["system_template"], "instance_template": data["instance_template"]}


def build_model(settings: dict):
    """Create the course model from settings."""
    config = dict(settings["model"])
    config["observation_template"] = settings["observation_template"]
    config["format_error_template"] = settings["format_error_template"]
    return get_model(config=config)


def template_vars(problem: Problem, mode: str, workspace: Path, tools_root: Path) -> dict:
    """Variables available in the prompt templates."""
    return {
        "mode": mode,
        "problem_id": problem.id,
        "title": problem.title,
        "spec": spec_markdown(problem, mode),
        "signature": problem.signature,
        "func_name": problem.func_name,
        "test_file": f"tests/{TEST_FILES[mode]}",
        "tools": toolbox.describe(mode, workspace, tools_root),
    }


def run_problem(
    problem: Problem,
    mode: str,
    out_dir: Path,
    *,
    model=None,
    settings: dict | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    tools_root: Path = toolbox.TOOLS_ROOT,
    blackbox_tests: Path | None = None,
    keep_workspace: bool = False,
) -> dict:
    """Run the agent on one problem and save its tests, trajectory and usage to out_dir."""
    settings = settings or load_settings()
    limits = mode_limits(settings, mode)
    model = model or build_model(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace = create_workspace(problem, mode, blackbox_tests)
    env = LocalEnvironment(
        cwd=str(workspace),
        env=toolbox.environment(mode, tools_root),
        timeout=limits["command_timeout_seconds"],
    )
    agent = TestingAgent(
        model,
        env,
        token_limit=limits["token_limit"],
        step_limit=limits["step_limit"],
        cost_limit=limits["cost_limit"],
        wall_time_limit_seconds=limits["wall_time_limit_seconds"],
        output_path=out_dir / "trajectory.json",
        **load_prompts(mode, prompts_dir),
    )
    try:
        result = agent.run(**template_vars(problem, mode, workspace, tools_root))
        exit_status = result.get("exit_status", "")
    except Exception as error:
        exit_status = f"Error: {type(error).__name__}: {error}"
    tests = collect_tests(workspace, out_dir / "tests")
    usage = {
        "problem_id": problem.id,
        "mode": mode,
        "exit_status": exit_status,
        "api_calls": agent.n_calls,
        "prompt_tokens": agent.prompt_tokens,
        "completion_tokens": agent.completion_tokens,
        "total_tokens": agent.total_tokens,
        "cost_usd": round(agent.cost, 6),
        "test_files": [t.name for t in tests],
    }
    (out_dir / "usage.json").write_text(json.dumps(usage, indent=2))
    if keep_workspace:
        usage["workspace"] = str(workspace)
    else:
        shutil.rmtree(workspace, ignore_errors=True)
    return usage


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["blackbox", "whitebox", "combined"], required=True)
    parser.add_argument("--run", required=True, help="name of the run; results go to runs/<run>/<mode>/")
    parser.add_argument("--problems", default=str(REPO_ROOT / "data" / "dev"), help="problem file or folder")
    parser.add_argument("--only", nargs="*", help="problem ids to run")
    parser.add_argument("--student", default=str(STUDENT_DIR), help="folder with prompts/ and tools/")
    parser.add_argument("--workers", type=int, default=1, help="problems to run in parallel")
    parser.add_argument("--keep-workspace", action="store_true", help="keep the temporary workspaces for debugging")
    args = parser.parse_args(argv)

    run_dir = RUNS_DIR / args.run
    student = Path(args.student).resolve()
    problems = load_problems(Path(args.problems), args.only)
    settings = load_settings()

    def run_one(problem: Problem) -> dict:
        blackbox_tests = run_dir / "blackbox" / problem.id / "tests" / TEST_FILES["blackbox"]
        usage = run_problem(
            problem,
            args.mode,
            run_dir / args.mode / problem.id,
            settings=settings,
            prompts_dir=student / "prompts",
            tools_root=student / "tools",
            blackbox_tests=blackbox_tests if args.mode == "whitebox" else None,
            keep_workspace=args.keep_workspace,
        )
        print(
            f"[{args.mode}] {problem.id}: {usage['exit_status']}  calls={usage['api_calls']}"
            f"  tokens={usage['total_tokens']}  cost=${usage['cost_usd']:.4f}  files={usage['test_files']}",
            flush=True,
        )
        return usage

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(run_one, problems))
    totals = {key: sum(row[key] for row in rows) for key in ("prompt_tokens", "completion_tokens", "cost_usd")}
    summary = {"run": args.run, "mode": args.mode, "student": str(student), "problems": rows, "totals": totals}
    (run_dir / args.mode).mkdir(parents=True, exist_ok=True)
    (run_dir / args.mode / "usage.json").write_text(json.dumps(summary, indent=2))
    print(
        f"Done. {len(rows)} problems, {totals['prompt_tokens'] + totals['completion_tokens']} tokens,"
        f" ${totals['cost_usd']:.4f}. Usage saved to {run_dir / args.mode / 'usage.json'}"
    )


if __name__ == "__main__":
    main()
