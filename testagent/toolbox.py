"""Discovery of the command line tools students give their agent."""

import os
import subprocess
import sys
from pathlib import Path

from testagent import REPO_ROOT

TOOLS_ROOT = REPO_ROOT / "student" / "tools"
HELP_LINES = 25


def tool_dirs(mode: str, tools_root: Path = TOOLS_ROOT) -> list[Path]:
    """Tool folders visible in a mode: the mode folder first, then shared.

    Combined mode sees its own folder, then the black-box and white-box tools, then shared.
    """
    if mode == "combined":
        return [tools_root / "combined", tools_root / "blackbox", tools_root / "whitebox", tools_root / "shared"]
    return [tools_root / mode, tools_root / "shared"]


def discover(mode: str, tools_root: Path = TOOLS_ROOT) -> list[Path]:
    """Executable tools for a mode. A mode tool shadows a shared tool with the same name."""
    found = {}
    for folder in tool_dirs(mode, tools_root):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.name.startswith((".", "_")) or path.suffix == ".md" or not path.is_file():
                continue
            if os.access(path, os.X_OK) and path.name not in found:
                found[path.name] = path
    return list(found.values())


SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def environment(mode: str, tools_root: Path = TOOLS_ROOT) -> dict[str, str]:
    """Environment for agent commands: tools and this Python on PATH, every secret blanked."""
    folders = [str(p) for p in tool_dirs(mode, tools_root)]
    folders.append(str(Path(sys.executable).parent))
    blanked = {name: "" for name in os.environ if any(marker in name.upper() for marker in SECRET_MARKERS)}
    return blanked | {
        "PATH": os.pathsep.join(folders + [os.environ.get("PATH", "")]),
        "PAGER": "cat",
        "MANPAGER": "cat",
        "PIP_PROGRESS_BAR": "off",
        "TQDM_DISABLE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def describe(mode: str, workspace: Path, tools_root: Path = TOOLS_ROOT) -> str:
    """Markdown list of tools built from each tool's --help output."""
    env = os.environ | environment(mode, tools_root)
    sections = []
    for tool in discover(mode, tools_root):
        try:
            result = subprocess.run(
                [str(tool), "--help"], cwd=workspace, env=env, capture_output=True, text=True, timeout=20
            )
            text = (result.stdout or result.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as error:
            text = f"(could not read --help: {error})"
        text = "\n".join(text.splitlines()[:HELP_LINES]) or "(no help text)"
        sections.append(f"### {tool.name}\n```\n{text}\n```")
    return "\n\n".join(sections) if sections else "(no tools available)"
