"""Path goals in a file: pairs of consecutive decision outcomes and loop iteration counts.

A decision outcome is an arc leaving an if, while or for line. Two outcomes taken one after the
other in the same call form an edge pair. Each loop also has three goals: it ran zero, one or many
times. This is edge-pair coverage plus loop-boundary coverage.
"""

import ast
import os
import sys


def decision_lines(source: str) -> tuple[set[int], set[int]]:
    """Line numbers of if, while and for statements, and of the loops among them."""
    decisions, loops = set(), set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.If, ast.While, ast.For)):
            decisions.add(node.lineno)
        if isinstance(node, (ast.While, ast.For)):
            loops.add(node.lineno)
    return decisions, loops


def _bucket(count: int) -> str:
    iterations = max(0, count - 1)
    return "0" if iterations == 0 else "1" if iterations == 1 else "many"


class PathTracer:
    """Records the path goals reached in one file between start() and stop(), and counts calls into it."""

    def __init__(self, target: str, decisions: set[int], loops: set[int]):
        self.target = os.path.realpath(target)
        self.decisions = decisions
        self.loops = loops

    def start(self) -> None:
        self.calls = 0
        self.goals: set[str] = set()
        self.frames: dict[int, dict] = {}
        sys.settrace(self._global)

    def stop(self) -> list[str]:
        sys.settrace(None)
        return sorted(self.goals)

    def _global(self, frame, event, arg):
        if os.path.realpath(frame.f_code.co_filename) == self.target:
            caller = frame.f_back
            if caller is None or os.path.realpath(caller.f_code.co_filename) != self.target:
                self.calls += 1
            return self._local
        return None

    def _arc(self, state: dict, arc: str) -> None:
        if state["previous"] is not None:
            self.goals.add(f"pair {state['previous']} {arc}")
        state["previous"] = arc

    def _local(self, frame, event, arg):
        state = self.frames.setdefault(id(frame), {"last": None, "previous": None, "counts": {}})
        if event == "line":
            line = frame.f_lineno
            if state["last"] in self.decisions:
                self._arc(state, f"{state['last']}>{line}")
            if line in self.loops:
                state["counts"][line] = state["counts"].get(line, 0) + 1
            state["last"] = line
        elif event == "return":
            if state["last"] in self.decisions:
                self._arc(state, f"{state['last']}>exit")
            for line in self.loops:
                if line in state["counts"]:
                    self.goals.add(f"loop {line} {_bucket(state['counts'][line])}")
            self.frames.pop(id(frame), None)
        return self._local
