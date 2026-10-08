"""Per-problem workspaces the agent works in."""

import re
import shutil
import tempfile
from pathlib import Path

from testagent.problems import Problem

MODES = ("blackbox", "whitebox", "combined")
TEST_FILES = {"blackbox": "test_blackbox.py", "whitebox": "test_whitebox.py", "combined": "test_combined.py"}


def strip_examples(spec: str) -> str:
    """Remove the worked examples from a LeetCode-style statement, keeping the constraints."""
    start = re.search(r"\n\s*Example 1:", spec)
    end = re.search(r"\n\s*Constraints:", spec)
    if not start or not end or end.start() < start.start():
        return spec
    return spec[: start.start()].rstrip() + "\n\n" + spec[end.start():].lstrip()


def spec_markdown(problem: Problem, mode: str = "whitebox") -> str:
    """Render the SPEC.md file shown to the agent. Black-box mode gets no worked examples."""
    spec = strip_examples(problem.spec) if mode == "blackbox" else problem.spec
    return (
        f"# {problem.title}\n\n"
        f"{spec.strip()}\n\n"
        "## Signature\n\n"
        f"```python\nclass Solution:\n    {problem.signature}\n```\n"
    )


def create_workspace(problem: Problem, mode: str, blackbox_tests: Path | None = None) -> Path:
    """Create a fresh temporary workspace for one problem in the given mode."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    root = Path(tempfile.mkdtemp(prefix=f"testagent-{mode}-{problem.id}-"))
    (root / "tests").mkdir()
    (root / "SPEC.md").write_text(spec_markdown(problem, mode))
    if mode == "blackbox":
        (root / "solution.py").write_text(problem.stub_solution())
    else:
        (root / "solution.py").write_text(problem.reference_solution)
        if mode == "whitebox" and blackbox_tests and blackbox_tests.exists():
            shutil.copy(blackbox_tests, root / "tests" / TEST_FILES["blackbox"])
    return root


def collect_tests(workspace: Path, destination: Path) -> list[Path]:
    """Copy every test file the agent wrote into the results directory."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in sorted((workspace / "tests").glob("*.py")):
        target = destination / path.name
        shutil.copy(path, target)
        copied.append(target)
    return copied
