"""A small, safe expression language for custom agents: conditions ("run when ...") and `{{ ... }}` pieces inside prompts.

It reads values and compares or reshapes them; it cannot do anything else. There is no assignment, import, loop, comprehension, lambda,
f-string, or call to anything outside a short list, and no name or attribute that starts with an underscore. Expressions are length and
size limited, so a definition cannot hang a run. This is deliberately not Python or JavaScript: code that needs to run belongs on an
administrator-approved external service, not inside the platform.

    evaluate("len(history) > 3 and amount.value >= 100", {"history": [...], "amount": {"value": 120}})  ->  True
    render_template("Refund of {{ amount.value }} for {customer}", {...})

Supported: numbers, strings, true/false/null, lists, `and or not`, comparisons (incl. `in`), `+ - * / %`, `a if c else b`, indexing,
`.field` access on mappings, and the functions below (also callable as methods on text: `name.lower()`).
"""
from __future__ import annotations

import ast
import re
from typing import Any

MAX_LEN = 500
MAX_NODES = 120

_FUNCS = {
    "len": len, "abs": abs, "min": min, "max": max, "round": round, "str": str, "int": int, "float": float, "sum": sum,
    "lower": lambda s: str(s).lower(), "upper": lambda s: str(s).upper(), "trim": lambda s: str(s).strip(),
    "contains": lambda s, x: x in s, "startswith": lambda s, x: str(s).startswith(str(x)), "endswith": lambda s, x: str(s).endswith(str(x)),
    "replace": lambda s, a, b: str(s).replace(str(a), str(b)), "join": lambda sep, xs: str(sep).join(str(x) for x in xs),
    "default": lambda v, d: d if v in (None, "") else v, "count": lambda xs, x=None: len(xs) if x is None else sum(1 for i in xs if i == x),
}
_METHODS = {"lower", "upper", "trim", "strip", "startswith", "endswith", "replace"}
_CONSTS = {"true": True, "false": False, "null": None, "True": True, "False": False, "None": None}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b, ast.Mod: lambda a, b: a % b}
_CMPOPS = {ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
           ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


class ExprError(ValueError):
    """The expression is not allowed, cannot be read, or fails when it runs. The message is written for the person who wrote it."""


def _parse(expr: str) -> ast.AST:
    text = (expr or "").strip()
    if not text:
        raise ExprError("The expression is empty")
    if len(text) > MAX_LEN:
        raise ExprError(f"The expression is longer than {MAX_LEN} characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as err:
        raise ExprError(f"Cannot read the expression: {err.msg}") from err
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExprError("The expression is too complex")
    return tree.body


def _ok_name(name: str) -> None:
    if name.startswith("_"):
        raise ExprError(f"Names starting with an underscore are not allowed: {name}")


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value
        raise ExprError("That kind of value is not allowed")
    if isinstance(node, ast.Name):
        _ok_name(node.id)
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ExprError(f"Unknown value '{node.id}'. Use one of: {', '.join(sorted(k for k in env if not k.startswith('_'))) or 'no values are available'}")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env) for e in node.elts]
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
        raise ExprError("That operator is not allowed")
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ExprError("That operator is not allowed")
        try:
            return op(_eval(node.left, env), _eval(node.right, env))
        except (TypeError, ZeroDivisionError) as err:
            raise ExprError(f"Cannot compute that: {err}") from err
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            fn = _CMPOPS.get(type(op))
            if fn is None:
                raise ExprError("That comparison is not allowed")
            right = _eval(comp, env)
            try:
                if not fn(left, right):
                    return False
            except TypeError as err:
                raise ExprError(f"Cannot compare those values: {err}") from err
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if isinstance(node, ast.Attribute):
        _ok_name(node.attr)
        base = _eval(node.value, env)
        if isinstance(base, dict):
            return base.get(node.attr)
        raise ExprError(f"'.{node.attr}' can only be used on an object")
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, env)
        key = _eval(node.slice, env)
        try:
            return base[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.Call):
        if node.keywords:
            raise ExprError("Named arguments are not allowed")
        args = [_eval(a, env) for a in node.args]
        if isinstance(node.func, ast.Name):
            _ok_name(node.func.id)
            fn = _FUNCS.get(node.func.id)
            if fn is None:
                raise ExprError(f"'{node.func.id}' is not a function you can use. Allowed: {', '.join(sorted(_FUNCS))}")
            call_args = args
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _METHODS:
            target = _eval(node.func.value, env)
            if not isinstance(target, str):
                raise ExprError(f".{node.func.attr}() works on text only")
            name = "trim" if node.func.attr == "strip" else node.func.attr
            fn, call_args = _FUNCS[name], [target, *args]
        else:
            raise ExprError("That call is not allowed")
        try:
            return fn(*call_args)
        except (TypeError, ValueError) as err:
            raise ExprError(f"Cannot use that function here: {err}") from err
    raise ExprError(f"'{type(node).__name__}' is not allowed in an expression")


def evaluate(expr: str, variables: dict[str, Any]) -> Any:
    return _eval(_parse(expr), dict(variables))


def truthy(expr: str, variables: dict[str, Any]) -> bool:
    """A condition: empty or 'always' is true."""
    text = (expr or "").strip()
    if not text or text.lower() in ("always", "true"):
        return True
    return bool(evaluate(text, variables))


def check(expr: str, names: set[str]) -> list[str]:
    """Problems with an expression, without running it: the syntax, and any value that is not one of `names`."""
    text = (expr or "").strip()
    if not text or text.lower() == "always":
        return []
    try:
        tree = _parse(text)
    except ExprError as err:
        return [str(err)]
    problems: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and not n.id.startswith("_") and n.id not in names and n.id not in _CONSTS and n.id not in _FUNCS:
            problems.append(f"Unknown value '{n.id}'")
        elif isinstance(n, ast.Name) and n.id.startswith("_"):
            problems.append(f"Names starting with an underscore are not allowed: {n.id}")
        elif isinstance(n, (ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.JoinedStr, ast.Await, ast.Yield, ast.NamedExpr)):
            problems.append(f"'{type(n).__name__}' is not allowed in an expression")
    # the full evaluator catches the remaining disallowed forms on a dry run with empty values
    if not problems:
        try:
            _eval(tree, {k: None for k in names})
        except ExprError as err:
            msg = str(err)
            if not msg.startswith("Cannot") and "Unknown value" not in msg:
                problems.append(msg)
        except Exception:  # noqa: BLE001 - a runtime type problem on empty values is not a definition problem
            pass
    return list(dict.fromkeys(problems))


_BRACE = re.compile(r"\{\{(.+?)\}\}", re.S)
_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def render_template(text: str, variables: dict[str, Any]) -> str:
    """Fill `{name}` and `{{ expression }}` in a prompt. An unknown {name} is left as written so the problem is visible."""
    def expr(m: re.Match[str]) -> str:
        try:
            return stringify(evaluate(m.group(1), variables))
        except ExprError as err:
            return f"[expression error: {err}]"

    out = _BRACE.sub(expr, text)
    return _VAR.sub(lambda m: stringify(variables[m.group(1)]) if m.group(1) in variables else m.group(0), out)


def variables_in(text: str) -> list[str]:
    """The `{name}` variables a prompt uses (not the ones inside `{{ }}`, which `names_in_expressions` reads)."""
    stripped = _BRACE.sub("", text or "")
    return list(dict.fromkeys(_VAR.findall(stripped)))


def names_in_expressions(text: str) -> set[str]:
    out: set[str] = set()
    for m in _BRACE.finditer(text or ""):
        try:
            for n in ast.walk(_parse(m.group(1))):
                if isinstance(n, ast.Name) and n.id not in _CONSTS and n.id not in _FUNCS:
                    out.add(n.id)
        except ExprError:
            continue
    return out


def expression_problems(text: str, names: set[str]) -> list[str]:
    problems: list[str] = []
    for m in _BRACE.finditer(text or ""):
        problems.extend(check(m.group(1), names))
    return problems
