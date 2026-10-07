"""The private browser's job: one box from open to close (services/browser/).

Its own timeout, not the worker's default five minutes: a task may run for
``BROWSER_MAX_MINUTES`` and wait on a person inside that. Tried once: a
browser that died part-way through someone's form is not re-run blind
(``session.run`` refuses a session that is no longer starting anyway).
"""

from __future__ import annotations

from arq.worker import func

from api import constants


async def run_browser_session(_ctx, session_uuid: str, start_url: str = "") -> None:
    from api.services.browser import session

    await session.run(str(session_uuid), str(start_url or ""))


run_browser_session_job = func(
    run_browser_session,
    name="run_browser_session",
    timeout=constants.BROWSER_MAX_MINUTES * 60 + 300,
    max_tries=1,
)


async def sweep_browser_sessions(_ctx) -> None:
    """Every few minutes: a session whose job died is ended and says so. Runs
    whatever the flag: switching the browser off must not leave a session
    that was running read "working" for ever."""
    from api.services.browser import session

    await session.sweep()
