"""What code can tell about a Python file an agent plans to write.

A small model asked to add a function to a file will, often enough, write
the file again with only the new function in it, put its tests in the wrong
file, forget an import, or write something that is not Python at all. None
of that needs a model to notice.

Only what needs a Python parser is here: whether the text is Python at all,
whether it uses a name it never defines, and whether what it imports from
another file of the project is there. What can be told by reading lines,
such as a function the file had and no longer has, is not: an agent's own
program says that, in Kosika, with the `check` of its agent declaration.

Nothing here runs the code. It is parsed, never imported or executed.
"""

from __future__ import annotations

import ast
import builtins
from typing import Callable, Mapping

_BUILTINS = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def is_python(path: str) -> bool:
    return path.lower().endswith(".py")


def _parsed(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def _module_names(tree: ast.Module) -> set[str]:
    """Every name a module binds at its top level, however it does so."""

    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        else:
            names.update(
                inner.id
                for inner in ast.walk(node)
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Store)
            )
    return names


def undefined_names(text: str) -> list[str]:
    """Names a file uses that it never defines or imports, in order of use.

    This is deliberately forgiving: a name bound anywhere in the file, as a
    function, an argument, a loop variable or an import, counts as defined
    everywhere in it. So it finds the plain mistake, a function called that
    was never imported, and stays quiet about anything subtler. A file with
    `from x import *` is not judged at all.
    """

    tree = _parsed(text)
    if tree is None:
        return []
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return []
            bound.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            bound.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            bound.add(node.name)
    missing: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in bound
            and node.id not in _BUILTINS
            and node.id not in missing
        ):
            missing.append(node.id)
    return missing


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def python_problems(
    path: str,
    new: str,
    module_text: Callable[[str], str | None],
) -> list[str]:
    """What is wrong with one Python file a plan would write.

    `module_text` gives the text another module of the project will have
    once the plan is done, by its name (`calc` for calc.py), or None when
    it is not one of the project's own files.
    """

    try:
        tree = ast.parse(new)
    except SyntaxError as error:
        where = f" at line {error.lineno}" if error.lineno else ""
        return [f"{path}: it is not valid Python{where}: {error.msg}"]
    except (ValueError, RecursionError):
        return [f"{path}: it is not valid Python"]
    problems: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
            continue
        other = module_text(node.module)
        if other is None:
            continue
        there = _parsed(other)
        if there is None:
            continue
        has = _module_names(there)
        lacking = [
            alias.name
            for alias in node.names
            if alias.name != "*" and alias.name not in has
        ]
        if lacking:
            problems.append(
                f"{path}: it imports {_listed(lacking)} from {node.module}, "
                f"but {node.module}.py does not define {'it' if len(lacking) == 1 else 'them'}"
            )
    missing = undefined_names(new)
    if missing:
        problems.append(
            f"{path}: it uses {_listed(missing[:5])}, which {'is' if len(missing) == 1 else 'are'} "
            "not defined or imported in the file"
        )
    return problems


def plan_code_problems(
    files: Mapping[str, str],
    before: Callable[[str], str | None],
) -> list[str]:
    """What is wrong with the Python files of a plan, taken together.

    `files` is every path the plan writes with its whole new text. `before`
    gives a file as it is now, or None when there is none.
    """

    python = {path: text for path, text in files.items() if is_python(path)}
    if not python:
        return []
    planned = {
        path.replace("\\", "/").removeprefix("./"): text
        for path, text in python.items()
    }

    def module_text(module: str) -> str | None:
        name = module.replace(".", "/") + ".py"
        if name in planned:
            return planned[name]
        return before(name)

    problems: list[str] = []
    for path, text in python.items():
        problems.extend(python_problems(path, text, module_text))
    return problems
