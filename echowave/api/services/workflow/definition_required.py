"""A bot with no version to run, said once and said clearly.

Three places reach for ``definition.workflow_json`` on an object that can be
``None``: the voice pipeline, the text runner, and duplicate. Each produced
``AttributeError: 'NoneType' object has no attribute 'workflow_json'`` -- a
500 with a stack trace, on a live call, for a condition that is not a bug in
the request but a fact about the bot.

A workflow row can exist with no definition bound to it. ``create_workflow_run``
resolves draft, then ``released_definition_id``, then the ``is_current`` row,
and binds ``None`` when all three miss -- a publish that failed halfway, a
migration that left the pointer behind, a row made by hand. Rare, and the
runtime is exactly where "rare" gets found.

The point is not to swallow it. A bot with no graph cannot take the call and
the call must fail; it should just fail as the sentence an operator can act
on rather than as a type error, and it should name the bot so somebody knows
which one to republish.
"""

from __future__ import annotations

from typing import Any, Optional


class NoDefinition(Exception):
    """This bot has no version to run."""

    def __init__(self, workflow_id: Optional[int], name: Optional[str] = None):
        self.workflow_id = workflow_id
        self.name = name
        who = f"'{name}'" if name else f"id {workflow_id}"
        super().__init__(
            f"Agent {who} has no published version to run. "
            "Open it and publish it, then try again."
        )


def require(definition: Any, *, workflow_id: Optional[int], name: Any = None):
    """Return the definition, or raise :class:`NoDefinition`.

    Takes the loaded object rather than an id: every caller has already
    resolved it, and a second lookup here would be a second chance to
    resolve a different one.
    """
    if definition is None:
        raise NoDefinition(workflow_id, str(name) if name else None)
    return definition
