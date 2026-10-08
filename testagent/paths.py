"""Path goals in a file: pairs of consecutive decision outcomes and loop iteration counts.

A decision outcome is an arc leaving an if, while or for line. Two outcomes taken one after the
other in the same call form an edge pair. Each time a loop starts, it adds one goal: it ran zero,
one or many times. This is edge-pair coverage plus loop-boundary coverage.
"""

import ast
import os
import sys


def decision_lines(source: str) -> tuple[set[int], dict[int, int]]:
    """Line numbers of if, while and for statements, and each loop's header and last line."""
    decisions, loops = set(), {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.If, ast.While, ast.For)):
            decisions.add(node.lineno)
        if isinstance(node, (ast.While, ast.For)):
            loops[node.lineno] = node.end_lineno
    return decisions, loops


def _bucket(count: int) -> str:
    iterations = max(0, count - 1)
    return "0" if iterations == 0 else "1" if iterations == 1 else "many"


class PathTracer:
    """Records the path goals reached in one file between start() and stop()."""

    def __init__(self, target: str, decisions: set[int], loops: dict[int, int]):
        self.target = os.path.realpath(target)
        self.decisions = decisions
        self.loops = loops

    def start(self) -> None:
        self.goals: set[str] = set()
        self.frames: dict[int, dict] = {}
        sys.settrace(self._global)

    def stop(self) -> list[str]:
        sys.settrace(None)
        for state in self.frames.values():
            self._flush(state)
        return sorted(self.goals)

    def _global(self, frame, event, arg):
        if os.path.realpath(frame.f_code.co_filename) == self.target:
            return self._local
        return None

    def _arc(self, state: dict, arc: str) -> None:
        if state["previous"] is not None:
            self.goals.add(f"pair {state['previous']} {arc}")
        state["previous"] = arc

    def _flush(self, state: dict) -> None:
        for line, count in state["counts"].items():
            self.goals.add(f"loop {line} {_bucket(count)}")
        state["counts"] = {}

    def _local(self, frame, event, arg):
        state = self.frames.setdefault(id(frame), {"last": None, "previous": None, "counts": {}})
        if event == "line":
            line = frame.f_lineno
            last = state["last"]
            if last in self.decisions:
                self._arc(state, f"{last}>{line}")
            if line in self.loops:
                inside = last is not None and line < last <= self.loops[line]
                if inside and line in state["counts"]:
                    state["counts"][line] += 1
                else:
                    if line in state["counts"]:
                        self.goals.add(f"loop {line} {_bucket(state['counts'][line])}")
                    state["counts"][line] = 1
            state["last"] = line
        elif event == "return":
            if state["last"] in self.decisions:
                self._arc(state, f"{state['last']}>exit")
            self._flush(state)
            self.frames.pop(id(frame), None)
        return self._local
