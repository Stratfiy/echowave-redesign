"""What went wrong on a run, in a shape the UI can render.

``event_handlers._record_pipeline_error`` has written the cause of a fatal
pipeline failure onto ``workflow_run.extra`` since it was added, with the
stated intent that the UI could show it. Nothing exposed it: ``extra`` is not
on ``WorkflowRunResponseSchema`` and never was, so the breadcrumb reached the
database and stopped there. A support question about a failed call was answered
by reading API logs, which is exactly the situation the record was added to
end.

``extra`` stays unexposed, deliberately. It is an open JSONB bag holding
storage keys, recording backends and whatever the next feature puts there —
publishing it wholesale would make every future key an accidental part of the
public API. This reads the one entry that answers "why did my call fail" and
gives it a name and a shape.
"""

from __future__ import annotations

from typing import Any


def get_pipeline_error(extra: dict | None) -> dict[str, Any] | None:
    """The recorded cause of a fatal pipeline failure, if there was one.

    ``None`` when the run did not fail this way, when the key predates the
    recording, or when it holds something other than the object written by
    ``_record_pipeline_error`` — a run that failed is never worth a 500 on the
    screen someone opened to find out why.
    """
    recorded = (extra or {}).get("pipeline_error")
    if not isinstance(recorded, dict):
        return None

    detail = recorded.get("detail")
    if not isinstance(detail, str) or not detail:
        return None

    error: dict[str, Any] = {"detail": detail}
    for key in ("frame_type", "at"):
        value = recorded.get(key)
        if isinstance(value, str) and value:
            error[key] = value
    return error
