"""Deterministic compute — the `calculate` skill.

Core principle: the LLM plans; *code* computes. The model must route every
arithmetic operation through this skill so numbers are exact and reproducible,
never hallucinated. Evaluation is a whitelisted AST walk (NOT `eval`), so a
crafted "expression" can't execute arbitrary code.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

_BINOPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: float(a // b),
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}

_UNARYOPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
}


class CalcError(ValueError):
    """Raised when an expression is unsupported or invalid."""


def _eval(node: ast.expr) -> float:
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalcError(f"unsupported value: {value!r}")
        return float(value)
    if isinstance(node, ast.BinOp):
        binop = _BINOPS.get(type(node.op))
        if binop is None:
            raise CalcError("unsupported operator")
        return binop(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        unop = _UNARYOPS.get(type(node.op))
        if unop is None:
            raise CalcError("unsupported operator")
        return unop(_eval(node.operand))
    raise CalcError("unsupported expression")


def safe_eval(expression: str) -> float:
    """Evaluate an arithmetic expression via a whitelisted AST walk."""
    tree = ast.parse(expression, mode="eval")
    return _eval(tree.body)


class Calculate:
    name = "calculate"
    description = (
        "Evaluate an arithmetic expression EXACTLY (+, -, *, /, //, %, ** and parentheses). "
        "Use this for ALL arithmetic — never compute numbers yourself."
    )
    dangerous = False
    untrusted = False
    parameters: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "expression": {"type": "string", "description": "The arithmetic expression."}
        },
        "required": ["expression"],
    }

    def run(self, args: dict[str, Any]) -> str:
        expr = args.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            return "error: 'expression' is required and must be a non-empty string"
        try:
            result = safe_eval(expr)
        except (CalcError, SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
            return f"error: could not evaluate '{expr}': {exc}"
        if result.is_integer():
            return str(int(result))
        return repr(result)
