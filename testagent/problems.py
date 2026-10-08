"""Problem loading."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Problem:
    """One function under test: its spec, signature and reference implementation."""

    id: str
    title: str
    func_name: str
    spec: str
    starter_code: str
    reference_solution: str
    difficulty: str = ""
    release_date: str = ""
    examples: list = field(default_factory=list)
    bugs: list = field(default_factory=list)
    equivalent_mutants: list = field(default_factory=list)
    hard_mutants: list = field(default_factory=list)
    mutant_kill_fraction: dict = field(default_factory=dict)
    reference_paths: list = field(default_factory=list)

    @property
    def signature(self) -> str:
        """The `def` line of the method under test."""
        for line in self.starter_code.splitlines():
            if line.strip().startswith("def "):
                return line.strip().rstrip(":") + ":"
        return ""

    def stub_solution(self) -> str:
        """Source of solution.py for black-box mode, with the body hidden."""
        return (
            "from typing import *\n\n\n"
            "class Solution:\n"
            f"    {self.signature}\n"
            '        raise NotImplementedError("The implementation is hidden in black-box mode.")\n'
        )


def load_problem(path: Path) -> Problem:
    """Load one problem from a JSON file."""
    data = json.loads(Path(path).read_text())
    return Problem(**data)


def load_problems(source: Path, only: list[str] | None = None) -> list[Problem]:
    """Load a problem file or every problem in a directory, optionally filtered by id."""
    source = Path(source)
    paths = sorted(source.glob("*.json")) if source.is_dir() else [source]
    problems = [load_problem(p) for p in paths]
    if only:
        wanted = set(only)
        problems = [p for p in problems if p.id in wanted]
    return problems


def clean_spec(text: str) -> str:
    """Normalize whitespace in a problem statement."""
    lines = [line.rstrip() for line in text.replace("\t", "    ").splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
