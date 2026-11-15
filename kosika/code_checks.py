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

Nothing here runs Python code. It is parsed, never imported or executed.

A Kosika file is held to more, because Kosika can be: it must check, and
every test written in it must pass, before it is ever written to disk. A
Kosika test reaches nothing (scripted answers only, no model, tool, file
or network), so running one is as safe as reading it.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
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


#: How many of a planned Kosika file's tests are run.
MAX_KOSIKA_TESTS = 40


def kosika_problems(files: Mapping[str, str]) -> list[str]:
    """What is wrong with the Kosika files of a plan: a file that does not
    check, or a test in it that fails. The files are written to a folder
    made for the purpose, each at its own path, so that one may import
    another; a file that imports one the plan does not hold is not judged.
    """

    kos = {
        path.replace("\\", "/").removeprefix("./"): text
        for path, text in files.items()
        if path.lower().endswith(".kos")
    }
    if not kos:
        return []
    import tempfile

    from .modules import load_program
    from .testing import run_tests

    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kosika-plan-") as scratch:
        top = Path(scratch)
        placed: dict[str, Path] = {}
        for path, text in kos.items():
            target = (top / path).resolve()
            if top.resolve() not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            placed[path] = target
        for path, target in placed.items():
            try:
                program = load_program(target)
            except OSError:
                # It imports a file that is not part of the plan.
                continue
            except (ValueError, TypeError, RecursionError) as exc:
                said = (
                    str(exc).replace(str(target) + ": ", "").replace(str(target), path)
                )
                problems.append(f"{path}: it is not valid Kosika: {said[:400]}")
                continue
            try:
                results = run_tests(program)[:MAX_KOSIKA_TESTS]
            except (ValueError, RuntimeError, RecursionError) as exc:
                problems.append(f"{path}: its tests could not be run: {str(exc)[:300]}")
                continue
            failed = [result for result in results if not result.passed]
            if failed:
                first = failed[0]
                problems.append(
                    f'{path}: its test "{first.name}" fails at line {first.line}: '
                    f"{str(first.message)[:300]}"
                    + (f" ({len(failed)} tests fail in all)" if len(failed) > 1 else "")
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
        return kosika_problems(files)
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
    return problems + kosika_problems(files)
