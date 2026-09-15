"""What makes a run a test, and who has to care (KAN-140, P0 item 5).

The office model's answer to "how does the bot know it is being tried?" is
that the bot does not decide and nobody flips a switch: **a run is a test
because a test verb started it.** The tester panel, a Hear it or Try it
card, and the eval runner all create their run through a path that stamps
``annotations["tester"]``; a browser call is ``mode == "webrtc"`` besides.
Everything downstream reads that stamp through one function, so the four
places that must treat a test differently cannot drift on what "test" means:

- the team's numbers do not count it (``agent_activity``);
- no outcome is filed against a real contact for it (``outcomes``);
- the business learns nothing from it (``organisation_learning``);
- its row on the thread says TEST (``agent_timeline.record_call_ended``).

It is still costed and still billed: the model ran and the minutes were
real. What is withheld is the *meaning*, not the price.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Run modes that are always a test. A browser call has no caller but the
#: person building the bot.
TEST_MODES = frozenset({"webrtc"})

#: The stamp. The text tester and the eval runner already wrote it before
#: this module existed; the voice tester now writes it too.
STAMP_KEY = "tester"


def stamp(source: str, modality: str) -> dict[str, Any]:
    """The annotation a test-creating path writes."""
    return {STAMP_KEY: {"source": source, "modality": modality}}


def is_test_annotations(
    annotations: Mapping[str, Any] | None, mode: str | None = None
) -> bool:
    if mode and str(mode) in TEST_MODES:
        return True
    return bool((annotations or {}).get(STAMP_KEY))


def is_test(run: Any) -> bool:
    """Whether a workflow run (model or anything with ``mode`` and
    ``annotations``) was started by a test verb."""
    if run is None:
        return False
    return is_test_annotations(
        getattr(run, "annotations", None), getattr(run, "mode", None)
    )


__all__ = ["STAMP_KEY", "TEST_MODES", "is_test", "is_test_annotations", "stamp"]
