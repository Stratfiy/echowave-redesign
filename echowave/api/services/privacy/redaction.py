"""What survives when a call's conversation is deleted.

Purging a run has always cleared the recording, the transcript, the gathered
context and the logs. It never cleared ``annotations`` — and annotations is
where the post-call pass writes what it *learned* from the conversation: the
QA summary, and ``extracted_data``, every named field the extraction library
pulled out of the person on the other end.

So a run could be purged for retention, or erased on a subject's own request,
and leave behind a structured record of that person: their name, their number,
their address, their order, whatever the agent was configured to collect. On
the erasure path in particular that is the wrong half to keep — the docstring
there reads "erase every trace of one person from an account's calls", and a
tidy JSON object of their details is more identifying than the audio was.

**Allowlist, not denylist.** The rule below names what may stay and drops
everything else, which is the opposite of how the rest of this file could have
been written. It matters because annotations is an open dict: the QA pass, the
disposition classifier, the integration step and the follow-up sender all write
into it, and the next thing somebody adds will land here without anybody
remembering this module exists. A denylist would retain that new key by
default. This retains nothing by default, and a reviewer adding a field to the
allowlist has to say out loud that it identifies nobody.

**What stays, and why it is not personal.** Closed-vocabulary labels and
numbers: the disposition codes an operations team filters on, the QA tags, the
score, the sentiment. These come from fixed sets the operator configured, not
from the caller. "This call was `booked`, tagged `pricing`, scored 4, ended
positive" describes the call; it does not describe a person, and losing it
would silently rewrite historical outcome analytics for every purged run.

**What goes.** Anything the model wrote in prose, and anything it lifted out of
the conversation: ``summary`` and ``extracted_data``. Also every key this
module does not recognise.
"""

from __future__ import annotations

from typing import Any

#: Top-level annotation keys that may survive a purge intact.
KEEP_TOP_LEVEL = frozenset({"tags"})

#: Inside a QA node's result. ``summary`` is free text about the conversation
#: and ``extracted_data`` is the person; neither is here.
KEEP_NODE_RESULT = frozenset({"tags", "score", "overall_sentiment"})

#: Inside the disposition block. The rest is provider/model/token bookkeeping
#: that the receipt already carries.
KEEP_DISPOSITION = frozenset({"dispositions"})

DISPOSITION_KEY = "disposition"
NODE_RESULTS_KEY = "node_results"


def _redact_node_result(node_result: Any) -> dict[str, Any]:
    if not isinstance(node_result, dict):
        return {}
    return {k: v for k, v in node_result.items() if k in KEEP_NODE_RESULT}


def _redact_qa_block(block: Any) -> dict[str, Any] | None:
    """A QA result: ``{"node_results": {node_id: {...}}}`` plus whatever else.

    Returns None when nothing survives, so the caller can drop the key rather
    than leave an empty shell that looks like a QA pass which found nothing.
    """
    if not isinstance(block, dict) or NODE_RESULTS_KEY not in block:
        return None
    nodes = block.get(NODE_RESULTS_KEY)
    if not isinstance(nodes, dict):
        return None
    redacted = {
        node_id: _redact_node_result(result) for node_id, result in nodes.items()
    }
    redacted = {node_id: r for node_id, r in redacted.items() if r}
    if not redacted:
        return None
    return {NODE_RESULTS_KEY: redacted}


def redact_annotations(annotations: Any) -> dict[str, Any]:
    """Strip everything derived from the conversation's content.

    Safe to run twice: redacting an already-redacted dict returns the same
    thing, which matters because the retention sweep retries a run whose
    storage delete failed and would otherwise re-process it.
    """
    if not isinstance(annotations, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key, value in annotations.items():
        if key in KEEP_TOP_LEVEL:
            cleaned[key] = value
            continue

        if key == DISPOSITION_KEY:
            if isinstance(value, dict):
                kept = {k: v for k, v in value.items() if k in KEEP_DISPOSITION}
                if kept:
                    cleaned[key] = kept
            continue

        qa = _redact_qa_block(value)
        if qa is not None:
            cleaned[key] = qa
        # Anything else — follow-up message bodies, integration responses, a
        # key added next year — is dropped. See the allowlist note above.

    return cleaned
