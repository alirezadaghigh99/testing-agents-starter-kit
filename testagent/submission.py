"""Check that a student/ folder follows the project rules.

Usage:
    python -m testagent.submission student/
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template, TemplateError

from testagent import toolbox

TEMPLATE_VARS = {
    "mode": "blackbox",
    "problem_id": "lcb-0",
    "title": "t",
    "spec": "s",
    "signature": "def f(self):",
    "func_name": "f",
    "test_file": "tests/test_blackbox.py",
    "tools": "",
    "task": "",
}
TOOL_FOLDERS = {"blackbox", "whitebox", "combined", "shared"}
FORBIDDEN = [
    (r"\bimport\s+(requests|httpx|aiohttp|socket|openai|anthropic|litellm)\b", "network or LLM client import"),
    (r"\bfrom\s+(requests|httpx|aiohttp|socket|openai|anthropic|litellm|urllib|http)\b", "network or LLM client import"),
    (r"\bimport\s+(urllib|http\.client)\b", "network import"),
    (r"\bminisweagent\b", "use of the agent framework inside a tool"),
    (r"\b(curl|wget|nc|ssh)\s", "network command"),
    (r"openrouter|api\.openai|anthropic\.com|OPENROUTER", "reference to an LLM service"),
    (r"/proc/|\.env\b|psutil", "reading other processes or secret files"),
]
MAX_BYTES = 1_000_000


def check_final(folder: Path) -> list[str]:
    path = folder / "final.yaml"
    if not path.exists():
        return [f"missing {path}: say which strategy to run on held-out problems (separate or combined)"]
    try:
        strategy = (yaml.safe_load(path.read_text()) or {}).get("strategy")
    except yaml.YAMLError as error:
        return [f"{path}: invalid YAML: {error}"]
    if strategy not in ("separate", "combined"):
        return [f"{path}: strategy must be separate or combined, got {strategy!r}"]
    if strategy == "combined" and not (folder / "prompts" / "combined.yaml").exists():
        return [f"{path}: strategy is combined but prompts/combined.yaml is missing"]
    return []


def check_prompts(folder: Path) -> list[str]:
    errors = []
    modes = ["blackbox", "whitebox"] + (["combined"] if (folder / "prompts" / "combined.yaml").exists() else [])
    for mode in modes:
        path = folder / "prompts" / f"{mode}.yaml"
        if not path.exists():
            errors.append(f"missing {path}")
            continue
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as error:
            errors.append(f"{path}: invalid YAML: {error}")
            continue
        extra = set(data or {}) - {"system_template", "instance_template"}
        if extra:
            errors.append(f"{path}: unexpected keys {sorted(extra)}; only system_template and instance_template are used")
        for key in ("system_template", "instance_template"):
            try:
                Template(str((data or {}).get(key, "")), undefined=StrictUndefined).render(**TEMPLATE_VARS)
            except TemplateError as error:
                errors.append(f"{path}: {key} does not render: {error}")
    return errors


def check_tools(folder: Path) -> list[str]:
    errors = []
    tools_root = folder / "tools"
    if not tools_root.is_dir():
        return [f"missing {tools_root}"]
    for entry in tools_root.iterdir():
        if entry.is_dir() and entry.name not in TOOL_FOLDERS:
            errors.append(f"{entry}: unknown tool folder; use blackbox/, whitebox/ or shared/")
    for sub in TOOL_FOLDERS:
        directory = tools_root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix == ".md" or "__pycache__" in path.parts:
                continue
            text = path.read_text(errors="replace")
            for pattern, reason in FORBIDDEN:
                if re.search(pattern, text):
                    errors.append(f"{path}: {reason} ({pattern})")
            if path.parent != directory or path.name.startswith((".", "_")):
                continue
            if not os.access(path, os.X_OK):
                errors.append(f"{path}: not executable (chmod +x)")
                continue
            if not text.startswith("#!"):
                errors.append(f"{path}: missing shebang line")
            try:
                result = subprocess.run(
                    [str(path), "--help"], capture_output=True, text=True, timeout=20,
                    env=os.environ | toolbox.environment(sub, tools_root),
                )
                if result.returncode != 0 or not (result.stdout or result.stderr).strip():
                    errors.append(f"{path}: `--help` must print help text and exit 0")
            except (OSError, subprocess.TimeoutExpired) as error:
                errors.append(f"{path}: `--help` failed: {error}")
    return errors


def check(folder: Path) -> list[str]:
    """All rule violations found in a student folder."""
    folder = Path(folder)
    errors = []
    for entry in folder.iterdir():
        if entry.name not in {"prompts", "tools", "final.yaml", "README.md"} and not entry.name.startswith("."):
            errors.append(f"{entry}: only prompts/, tools/, final.yaml and README.md are allowed in the student folder")
    size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    if size > MAX_BYTES:
        errors.append(f"{folder}: {size} bytes, the limit is {MAX_BYTES}")
    return errors + check_final(folder) + check_prompts(folder) + check_tools(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default="student")
    args = parser.parse_args(argv)
    errors = check(Path(args.folder))
    for error in errors:
        print(f"ERROR {error}")
    print("Submission OK." if not errors else f"{len(errors)} problem(s) found.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
