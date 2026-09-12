"""A guard against the one bug this codebase keeps making.

Three times in a week the same shape shipped: code doing exactly what it was
told, and something missing with nothing to show for it.

* A category allowlist with no entry for "commerce" silently dropped every
  shipping tracker from the integrations screen.
* An ``endswith("_mcp")`` filter silently dropped Cashfree, which Composio
  publishes only under that suffix.
* ``getattr(self._engine, "_workflow_id", None)`` silently wrote NULL into
  every row of a new table, because the engine has no such attribute.

The first two are now covered by tests about what must appear. This file
covers the third, which is the most dangerous of the three because it needs no
domain knowledge to introduce and produces no symptom at all: a default makes a
typo, a rename or an attribute that never existed indistinguishable from a
legitimately absent value, forever.

The rule this enforces is narrow on purpose. ``getattr`` with a default is
often right -- an optional attribute genuinely set later is exactly what it is
for. What is never right is naming an attribute the class does not have
anywhere, which is a typo wearing a safety net.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "services" / "workflow"
ENGINE_SOURCE = WORKFLOW_DIR / "pipecat_engine.py"


def _engine_attribute_names() -> set[str]:
    """Every name PipecatEngine ever assigns to self, plus its methods.

    Read from the source rather than by constructing one: the engine needs an
    LLM, a context and a pipeline to exist, and a guard that cannot run without
    building a call pipeline is a guard nobody runs.
    """
    tree = ast.parse(ENGINE_SOURCE.read_text())
    names: set[str] = set()

    for node in ast.walk(tree):
        # self.foo = ... and self.foo: T = ...
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)

        # def foo(...), async def foo(...), and @property foo
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)

    # setattr(self, "foo", ...) is rare but would otherwise read as absent.
    names.update(
        re.findall(r'setattr\(\s*self\s*,\s*["\'](\w+)["\']', ENGINE_SOURCE.read_text())
    )
    return names


def _engine_getattr_names() -> list[tuple[str, str, int]]:
    """Every ``getattr(self._engine, "name", ...)`` in the workflow package."""
    found: list[tuple[str, str, int]] = []
    for path in sorted(WORKFLOW_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "getattr" or len(node.args) < 2:
                continue
            target, name = node.args[0], node.args[1]
            if not (
                isinstance(target, ast.Attribute)
                and target.attr == "_engine"
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                continue
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                found.append((path.name, name.value, node.lineno))
    return found


class TestEngineAttributesAreReal:
    def test_the_guard_is_actually_looking_at_something(self):
        """If a refactor renames the engine or moves these files, the scan
        would quietly find nothing and pass forever -- which is the same bug
        this file exists to catch, one level up."""
        assert ENGINE_SOURCE.exists()
        assert len(_engine_attribute_names()) > 20
        assert _engine_getattr_names(), "no getattr(self._engine, ...) found at all"

    @pytest.mark.parametrize(
        "location", _engine_getattr_names(), ids=lambda case: f"{case[0]}:{case[1]}"
    )
    def test_every_name_read_off_the_engine_exists_on_it(self, location):
        """The failure this catches wrote NULL into every row of a new table
        and raised nothing. `_workflow_id` was never an engine attribute; the
        default made it look like one that happened to be unset."""
        filename, attribute, lineno = location
        known = _engine_attribute_names()
        assert attribute in known, (
            f"{filename}:{lineno} reads `{attribute}` off the engine with a "
            f"default, but PipecatEngine never sets it. Either the name is "
            f"wrong, or the attribute was removed and this quietly became a "
            f"constant None."
        )
