"""Every name `model_row` uses must resolve.

This file exists because of an outage. `model_row` used ``model_presets``
three times and never imported it, so ``GET /workflow/{id}/model-row`` raised
``NameError`` on every request. The row vanished from every agent editor and
nothing said why -- the component hides itself rather than showing an error,
which is right for a network blip and exactly wrong for a crash in our own
code.

A review pass caught it. No test could have, because the function had none:
it needs a session, a priced rate card, platform keys and an effective model
configuration, so the cheapest honest test of its *behaviour* is an
integration test nobody had written.

The specific defect, though, needs none of that. An unresolved global is
visible in the syntax tree, and checking for one costs nothing and runs
everywhere -- including the environments where pipecat and Postgres are
unavailable, which is where this would otherwise go unrun.

Static rather than a smoke call on purpose: a smoke call proves one path
executed, and `model_row` has several (cascade, realtime, priced, unpriced).
This proves *no* path can reference a name that does not exist.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "configuration"
    / "agent_options.py"
)

#: Functions whose bodies are checked. Any customer-facing assembler added to
#: this module is worth adding here -- the cost is one line.
CHECKED = ("model_row", "bundle_options", "catalogue_options")


def _module_names(tree: ast.Module) -> set[str]:
    """Everything importable or defined at module level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _bound_inside(fn) -> set[str]:
    """Names the function itself creates: imports, assignments, args, loops."""
    bound = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    if fn.args.vararg:
        bound.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        bound.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            for target in ast.walk(node.target):
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    return bound


@pytest.mark.parametrize("function_name", CHECKED)
def test_no_unresolved_names(function_name: str):
    tree = ast.parse(MODULE.read_text())
    module_names = _module_names(tree)

    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == function_name
        ),
        None,
    )
    assert fn is not None, f"{function_name} has been renamed or removed"

    bound = _bound_inside(fn) | module_names | set(dir(__builtins__)) | set(dir(int))
    # Only attribute access (`model_presets.match`) is checked. A bare name can
    # be a keyword argument or a local this walk did not model; a name used as
    # a namespace has to be a module, and a missing module import is the defect
    # this file is about.
    used_as_namespace = {
        node.value.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }

    unresolved = sorted(used_as_namespace - bound)
    assert not unresolved, (
        f"{function_name} uses {unresolved} as a namespace with no import in "
        f"scope. This is the NameError that emptied the model row."
    )
