"""AST mutation operators used by the evaluator.

Mutants are small automatic changes to the reference solution. The evaluator leaves out mutants
that no LiveCodeBench test detects (equivalent mutants) and reports hard mutants separately.
"""

import ast
import copy
import hashlib
from dataclasses import dataclass

BINOP_SWAPS = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Add,
    ast.FloorDiv: ast.Mult,
    ast.Div: ast.Mult,
    ast.Mod: ast.FloorDiv,
}
COMPARE_SWAPS = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
BOOLOP_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


@dataclass
class Mutant:
    """One mutated version of the source."""

    id: str
    operator: str
    line: int
    source: str


def _sites(tree: ast.AST) -> list[tuple[ast.AST, str, object]]:
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.BinOp, ast.AugAssign)) and type(node.op) in BINOP_SWAPS:
            sites.append((node, "arithmetic", BINOP_SWAPS[type(node.op)]))
        elif isinstance(node, ast.Compare):
            for index, op in enumerate(node.ops):
                if type(op) in COMPARE_SWAPS:
                    sites.append((node, "comparison", (index, COMPARE_SWAPS[type(op)])))
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOLOP_SWAPS:
            sites.append((node, "boolean", BOOLOP_SWAPS[type(node.op)]))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((node, "remove_not", None))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            sites.append((node, "constant_plus_one", node.value + 1))
            sites.append((node, "constant_minus_one", node.value - 1))
        elif isinstance(node, (ast.If, ast.While)):
            sites.append((node, "negate_condition", None))
        elif isinstance(node, ast.Break):
            sites.append((node, "break_to_continue", None))
        elif isinstance(node, ast.Continue):
            sites.append((node, "continue_to_break", None))
        elif isinstance(node, ast.Return) and node.value is not None:
            sites.append((node, "return_none", None))
    return sites


class _Apply(ast.NodeTransformer):
    def __init__(self, target_index: int, operator: str, payload: object):
        self.counter = -1
        self.target_index = target_index
        self.operator = operator
        self.payload = payload

    def _is_target(self, node: ast.AST) -> bool:
        return getattr(node, "_mutation_id", None) == self.target_index

    def generic_visit(self, node):
        node = super().generic_visit(node)
        if not self._is_target(node):
            return node
        op = self.operator
        if op in ("arithmetic", "boolean"):
            node.op = self.payload()
        elif op == "comparison":
            index, new_op = self.payload
            node.ops[index] = new_op()
        elif op == "remove_not":
            return node.operand
        elif op in ("constant_plus_one", "constant_minus_one"):
            return ast.copy_location(ast.Constant(self.payload), node)
        elif op == "negate_condition":
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        elif op == "break_to_continue":
            return ast.copy_location(ast.Continue(), node)
        elif op == "continue_to_break":
            return ast.copy_location(ast.Break(), node)
        elif op == "return_none":
            node.value = ast.Constant(None)
        return node


def generate_mutants(source: str, limit: int = 0) -> list[Mutant]:
    """All distinct single-point mutants of `source`, optionally capped at `limit`."""
    original_tree = ast.parse(source)
    original = ast.unparse(original_tree)
    seen = {original}
    mutants = []
    for index, (node, operator, payload) in enumerate(_sites(original_tree)):
        tree = copy.deepcopy(original_tree)
        target = _sites(tree)[index][0]
        target._mutation_id = index
        mutated = _Apply(index, operator, payload).visit(tree)
        ast.fix_missing_locations(mutated)
        try:
            text = ast.unparse(mutated)
            compile(text, "<mutant>", "exec")
        except (SyntaxError, ValueError):
            continue
        if text in seen:
            continue
        seen.add(text)
        mutant_id = hashlib.sha1(text.encode()).hexdigest()[:10]
        mutants.append(Mutant(mutant_id, operator, getattr(node, "lineno", 0), text + "\n"))
        if limit and len(mutants) >= limit:
            break
    return mutants
