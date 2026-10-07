"""One browser task, from Decibyl's tool call to the receipt.

``start`` is what the tool calls: it checks the task, writes the session
row and the panel's row on the thread, and queues the job. ``run`` is the
job (``tasks/browser.py``): it opens a box through the driver and stays with
it until it ends, answering the box's two questions -- "may I take this
step?" (the gate) and "what does the model say?" (the bridge) -- and the
person's presses, which arrive over Redis (``channel.py``).

States, all of them honest and all of them on the panel:

``starting``        the box is being opened
``working``         the browser is taking steps
``waiting_for_you`` an approval card is waiting, or the site needs you to
                    sign in
``captcha``         blocked by a CAPTCHA: Take over to solve it
``taken_over``      you have the browser; it waits for you to hand it back
``done``            it finished; the receipt says what it did
``failed``          it could not; the receipt says why
``stopped``         you stopped it, or nobody answered in time
``limit_reached``   it hit its step, minute or cost limit

The step list never holds what a person typed during Take over, and the
screenshot is never written to the database (``channel.py``).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants
from api.db import db_client
from api.enums import AgentEventActor, AgentEventKind
from api.services.browser import bridge, channel, cookies, drivers, gate, sites
from api.services.workflow import agent_timeline, untrusted

STARTING = "starting"
WORKING = "working"
WAITING = "waiting_for_you"
CAPTCHA = "captcha"
TAKEN_OVER = "taken_over"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
LIMIT = "limit_reached"
TERMINAL = (DONE, FAILED, STOPPED, LIMIT)
LIVE = (STARTING, WORKING, WAITING, CAPTCHA, TAKEN_OVER)

MAX_STEPS_SHOWN = 120
MAX_TASK_CHARS = 2000
POLL_SECONDS = 0.5

#: Approval cards for the browser are this action (``actions.py``).
STEP_ACTION = "browser_step"


class StartRefused(ValueError):
    """The task cannot start; said to the model, which tells the person."""


def limits_for(
    steps: int | None = None, minutes: int | None = None, cost_paise: int | None = None
) -> dict[str, int]:
    """The task's limits: what was asked for, never past the ceilings."""

    def bounded(value: int | None, default: int, ceiling: int) -> int:
        try:
            wanted = int(value) if value else default
        except (TypeError, ValueError):
            wanted = default
        return max(1, min(wanted, ceiling))

    return {
        "steps": bounded(
            steps, constants.BROWSER_DEFAULT_STEPS, constants.BROWSER_MAX_STEPS
        ),
        "minutes": bounded(
            minutes, constants.BROWSER_DEFAULT_MINUTES, constants.BROWSER_MAX_MINUTES
        ),
        "cost_paise": bounded(
            cost_paise,
            constants.BROWSER_DEFAULT_COST_PAISE,
            constants.BROWSER_MAX_COST_PAISE,
        ),
    }


def system_rules(sites_named: list[str], verbs: list[str]) -> str:
    """What the box's model is told on top of browser-use's own prompt."""
    lines = [
        "You are Decibyl's private browser, working for one person on one task.",
        untrusted.RULE,
        "Never type a password, PIN, one-time code or card details. When a "
        "site needs a sign-in, a code or a CAPTCHA, call ask_person with why, "
        "and wait: the person takes over, does it themselves and hands back.",
        "Every step that would submit, pay, send, book or sign up waits for "
        "the person to approve it on a card. If a step is refused, do not try "
        "another way to do the same thing; say what was refused and finish.",
    ]
    if sites_named:
        lines.append(
            "Stay on these sites: " + ", ".join(sites_named) + ". Others are refused."
        )
    if not verbs:
        lines.append(
            "This task is to look and report, not to act: do not submit, pay, "
            "send, book or sign up anything."
        )
    lines.append(
        "Finish with done, saying plainly what you found or did, with the links "
        "of the pages it came from."
    )
    return "\n".join(lines)


def _now() -> datetime:
    return datetime.now(UTC)


# --- the tool's half --------------------------------------------------------------


async def start(
    *,
    organization_id: int,
    user_id: int,
    thread_id: str | None,
    task: str,
    request: str,
    sites_named: list[str],
    verbs_declared: list[str] | None,
    start_url: str | None = None,
    steps: int | None = None,
    minutes: int | None = None,
) -> dict[str, Any]:
    """Open a browser for the person. Returns what the model is told."""
    task = (task or "").strip()[:MAX_TASK_CHARS]
    if not task:
        raise StartRefused("Say what the browser should do.")
    try:
        driver_name = drivers.configured()
        if not driver_name:
            raise drivers.BrowserUnavailable(
                "The private browser is not set up on this server yet."
            )
    except drivers.BrowserUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}

    rules = await sites.staff_rules()
    named: list[str] = []
    for raw in sites_named or []:
        site = sites.normalise_site(str(raw))
        if not site or site in named:
            continue
        decided = sites.decide(f"https://{site}/", rules)
        if not decided.allowed:
            raise StartRefused(decided.reason)
        named.append(site)
    named = named[:10]
    if start_url:
        decided = sites.decide(start_url, rules)
        if not decided.allowed:
            raise StartRefused(decided.reason)
        host = sites.host_of(start_url)
        if named and not sites.on_task(start_url, named):
            named.append(sites.normalise_site(host))
    if await db_client.count_live_browser_sessions() >= constants.BROWSER_MAX_LIVE:
        return {
            "status": "unavailable",
            "reason": "Every private browser is busy just now; try again in a few minutes.",
        }

    verbs = gate.allowed_verbs(request, verbs_declared)
    session_uuid = str(uuid.uuid4())
    row = await db_client.create_browser_session(
        session_uuid=session_uuid,
        organization_id=organization_id,
        user_id=user_id,
        thread_id=thread_id,
        task=task,
        request=(request or "")[:MAX_TASK_CHARS],
        sites=named,
        allowed_verbs=verbs,
        limits=limits_for(steps, minutes),
    )
    with agent_timeline.collecting() as written:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.BROWSER_SESSION.value,
            actor=AgentEventActor.AGENT.value,
            summary=f"Browsing: {task[:200]}",
            payload={
                "from": "Decibyl",
                "session_uuid": session_uuid,
                "by": user_id,
                "start_url": start_url or "",
            },
            in_channel=False,
            thread_id=thread_id,
        )
    event_id = written[-1][1] if written else None
    await db_client.update_browser_session(
        row.id,
        event_id=event_id,
        steps=[],
        used={"steps": 0, "minutes": 0, "cost_paise": 0},
    )

    from api.tasks.arq import enqueue_job
    from api.tasks.function_names import FunctionNames

    try:
        await enqueue_job(
            FunctionNames.RUN_BROWSER_SESSION, session_uuid, start_url or ""
        )
    except Exception as exc:  # noqa: BLE001 - said on the panel, not raised at the turn
        logger.error("Could not queue browser session {}: {}", session_uuid, exc)
        await db_client.update_browser_session(
            row.id,
            state=FAILED,
            state_note="The browser could not be started. This is us, not you.",
            ended_at=_now(),
        )
        return {
            "status": "error",
            "error": "The browser could not be started just now.",
        }
    allowed = ", ".join(gate.VERB_LABELS[v].lower() for v in verbs) or "nothing"
    return {
        "status": "started",
        "note": (
            "The private browser has started; the person watches it live on the "
            "panel in this thread and can take over. It will ask before it "
            f"does anything consequential (allowed here once approved: {allowed}). "
            "Say in one line that it is working, then end your reply. The result "
            "arrives on this thread when it finishes."
        ),
    }


# --- the job's half ----------------------------------------------------------------


class _Run:
    def __init__(self, row: Any, driver: drivers.Driver, start_url: str) -> None:
        self.row = row
        self.driver = driver
        self.start_url = start_url
        self.handle: str | None = None
        self.state = row.state
        self.note = row.state_note or ""
        self.steps: list[dict[str, Any]] = list(row.steps or [])
        self.used: dict[str, int] = {"steps": 0, "minutes": 0, "cost_paise": 0}
        self.limits: dict[str, int] = dict(row.limits or limits_for())
        self.pending: dict[str, Any] | None = None
        self.keep: list[str] = list(row.keep_login_sites or [])
        self.signals: list[str] = []
        self.signals_said: set[str] = set()
        self.pressed: list[dict[str, Any]] = []
        self.refused: list[str] = []
        self.links: list[str] = []
        self.current_url = start_url or ""
        self.result = ""
        self.loaded_logins: list[str] = []
        self.started = time.monotonic()
        self.wait_until: float | None = None
        self.dirty = True
        self.task = gate.Task(
            request=row.request or "",
            task=row.task or "",
            sites=list(row.sites or []),
            verbs=list(row.allowed_verbs or []),
            rules=[],
        )

    # -- bookkeeping --

    def say(self, text: str, kind: str = "note", **extra: Any) -> None:
        self.steps.append(
            {"at": _now().isoformat(), "kind": kind, "text": text[:400], **extra}
        )
        del self.steps[:-MAX_STEPS_SHOWN]
        self.dirty = True

    def set_state(self, state: str, note: str = "") -> None:
        if state != self.state or note != self.note:
            self.state, self.note = state, note
            self.dirty = True
        if state in (WAITING, CAPTCHA, TAKEN_OVER):
            if self.wait_until is None:
                self.wait_until = time.monotonic() + constants.BROWSER_WAIT_MINUTES * 60
        else:
            self.wait_until = None

    async def save(self) -> None:
        if not self.dirty:
            return
        self.used["minutes"] = int((time.monotonic() - self.started) // 60)
        await db_client.update_browser_session(
            self.row.id,
            state=self.state,
            state_note=self.note[:500],
            steps=self.steps,
            used=dict(self.used),
            pending=self.pending,
            keep_login_sites=self.keep,
        )
        self.dirty = False

    async def reply(self, call_id: Any, result: Any) -> None:
        assert self.handle is not None
        try:
            await self.driver.send(self.handle, {"reply_to": call_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - the box ended under us
            logger.warning("Browser box went away before a reply: {}", exc)

    # -- the box's questions --

    async def on_gate(self, message: dict[str, Any]) -> None:
        request = dict(message.get("gate") or {})
        call_id = message.get("id")
        self.current_url = str(request.get("url") or self.current_url)
        self.signals = gate.injection_signals(str(request.pop("page_text", "") or ""))
        request["signals"] = self.signals
        if self.signals and self.current_url not in self.signals_said:
            self.signals_said.add(self.current_url)
            self.say(
                "This page has text addressed to an assistant (“"
                + self.signals[0][:120]
                + "”). It is treated as information, not instructions.",
                kind="warning",
            )
        verdict = gate.check(request, self.task)
        if verdict.decision == gate.ALLOW:
            await self.reply(call_id, verdict.as_reply())
            return
        if verdict.decision == gate.REFUSE:
            self.refused.append(verdict.reason)
            self.say(verdict.reason, kind="refused")
            await self.reply(call_id, verdict.as_reply())
            return
        await self.ask(call_id, request, verdict)

    async def ask(
        self, call_id: Any, request: dict[str, Any], verdict: gate.Verdict
    ) -> None:
        """An approval card for the exact step, bound to its digest."""
        from api.services.workflow import actions

        gate_id = uuid.uuid4().hex
        element = gate.Element.of(request.get("element"))
        digest = gate.digest(request)
        payload = {
            "action": actions.BROWSER_STEP,
            "args": {
                "session_uuid": self.row.session_uuid,
                "gate_id": gate_id,
                "digest": digest,
                "verb": verdict.verb,
                "page_url": str(request.get("url") or "")[:500],
                "button": element.label(),
                "fields": gate.fields_shown(element),
                "form_action": element.form_action[:500],
            },
            "label": verdict.label,
            "why": f"Your browser task: {self.row.task[:200]}",
            "effect": verdict.effect,
            "reversible": False,
            "state": actions.PROPOSED,
            "requested_by": self.row.user_id,
        }
        with agent_timeline.collecting() as written:
            await agent_timeline.record(
                organization_id=self.row.organization_id,
                kind=AgentEventKind.ACTION_PROPOSED.value,
                summary=verdict.label,
                payload=payload,
                in_channel=False,
                thread_id=self.row.thread_id,
            )
        event_id = written[-1][1] if written else None
        if event_id is None:
            self.refused.append("The approval card could not be written; not done.")
            self.say(
                "The approval card could not be written, so nothing was pressed.",
                kind="refused",
            )
            await self.reply(
                call_id, {"decision": "refuse", "reason": "approval unavailable"}
            )
            return
        self.pending = {
            "gate_id": gate_id,
            "call_id": call_id,
            "digest": digest,
            "event_id": event_id,
            "label": verdict.label,
            "approved": False,
        }
        self.say(f"Asked you to approve: {verdict.label}", kind="ask")
        self.set_state(WAITING, f"Waiting for you to approve: {verdict.label}")

    async def on_llm(self, message: dict[str, Any]) -> bool:
        """Answer one model request. False ends the run."""
        call_id = message.get("id")
        if self.used["steps"] >= self.limits["steps"]:
            await self.reply(call_id, {"ok": False, "error": "step limit"})
            await self.finish(
                LIMIT, f"Stopped at the step limit ({self.limits['steps']} steps)."
            )
            return False
        if self.used["cost_paise"] >= self.limits["cost_paise"]:
            await self.reply(call_id, {"ok": False, "error": "cost limit"})
            await self.finish(LIMIT, "Stopped at the cost limit for one task.")
            return False
        try:
            response, cost = await bridge.call(
                dict(message.get("body") or {}),
                organization_id=self.row.organization_id,
                signals=self.signals,
            )
        except bridge.BridgeError as exc:
            await self.reply(call_id, {"ok": False, "error": str(exc)})
            await self.finish(FAILED, str(exc))
            return False
        self.used["cost_paise"] += int(cost)
        self.dirty = True
        await self.reply(call_id, {"ok": True, "response": response})
        return True

    # -- the box's news --

    async def on_message(self, message: dict[str, Any]) -> bool:
        kind = message.get("type")
        if kind == "hello":
            self.set_state(WORKING, "Working…")
        elif kind == "gate":
            await self.on_gate(message)
        elif kind == "llm":
            return await self.on_llm(message)
        elif kind == "step":
            self.used["steps"] += 1
            goal = str(message.get("goal") or "").strip() or "Next step"
            self.say(
                goal,
                kind="step",
                n=self.used["steps"],
                url=str(message.get("url") or "")[:500],
            )
            if self.used["steps"] > self.limits["steps"]:
                await self.finish(
                    LIMIT, f"Stopped at the step limit ({self.limits['steps']} steps)."
                )
                return False
        elif kind == "screen":
            self.current_url = str(message.get("url") or self.current_url)
            await channel.set_screen(
                self.row.session_uuid,
                {
                    "jpeg": message.get("jpeg") or "",
                    "w": message.get("w"),
                    "h": message.get("h"),
                    "url": self.current_url,
                    "title": str(message.get("title") or "")[:200],
                    "at": _now().isoformat(),
                },
            )
        elif kind == "state":
            state = str(message.get("state") or "")
            if state == "captcha":
                self.say("Blocked by a CAPTCHA.", kind="warning")
                self.set_state(
                    CAPTCHA,
                    "Blocked by a CAPTCHA. Take over to solve it, then hand back.",
                )
            elif state == "needs_person":
                why = str(message.get("note") or "This site needs you.")[:200]
                self.say(f"Needs you: {why}", kind="warning")
                self.set_state(
                    WAITING, f"{why} Take over, do it yourself, then hand back."
                )
            elif state == "taken_over":
                self.set_state(
                    TAKEN_OVER, "You have the browser. Hand it back when you are done."
                )
            elif state == "working" and self.state != WAITING:
                self.set_state(WORKING, "Working…")
        elif kind == "gate_result":
            gate_id = str(message.get("gate_id") or "")
            outcome = {
                "ok": bool(message.get("ok")),
                "note": str(message.get("note") or "")[:300],
                "url": str(message.get("url") or "")[:500],
            }
            await channel.push_outcome(gate_id, outcome)
            if self.pending and self.pending.get("gate_id") == gate_id:
                label = self.pending.get("label", "")
                self.pressed.append(
                    {"label": label, **outcome, "at": _now().isoformat()}
                )
                self.say(
                    f"Done: {label}. {outcome['note']}"
                    if outcome["ok"]
                    else f"Did not go through: {label}. {outcome['note']}",
                    kind="done" if outcome["ok"] else "refused",
                )
                self.pending = None
                self.set_state(WORKING, "Working…")
        elif kind == "done":
            self.result = str(message.get("result") or "")[:4000]
            self.links = [str(link)[:500] for link in (message.get("links") or [])][:10]
            if message.get("ok"):
                await self.finish(DONE, "Finished.")
            else:
                error = str(message.get("error") or "")[:300]
                await self.finish(
                    FAILED, error or "The browser could not finish the task."
                )
            return False
        elif kind == "exited":
            await self.finish(
                FAILED, "The browser stopped unexpectedly. This is us, not you."
            )
            return False
        return True

    # -- the person's presses --

    async def on_command(self, command: dict[str, Any]) -> bool:
        cmd = command.get("cmd")
        assert self.handle is not None
        if cmd == "stop":
            await self.finish(STOPPED, "You stopped it.")
            return False
        if cmd == "takeover":
            await self.driver.send(self.handle, {"cmd": "takeover"})
            self.say("You took over the browser.", kind="person")
            self.set_state(
                TAKEN_OVER, "You have the browser. Hand it back when you are done."
            )
        elif cmd == "input" and self.state == TAKEN_OVER:
            # Passed on and forgotten: never logged, never in the step list.
            await self.driver.send(
                self.handle,
                {
                    k: command[k]
                    for k in ("cmd", "kind", "x", "y", "text", "key", "dy")
                    if k in command
                },
            )
        elif cmd == "handback":
            if command.get("keep_login") and self.current_url:
                site = sites.site_of_url(self.current_url)
                if site and site not in self.keep:
                    self.keep.append(site)
                    self.say(f"You asked to stay signed in to {site}.", kind="person")
            await self.driver.send(self.handle, {"cmd": "handback"})
            self.say("You handed the browser back.", kind="person")
            if self.pending:
                self.set_state(
                    WAITING, f"Waiting for you to approve: {self.pending['label']}"
                )
            else:
                self.set_state(WORKING, "Working…")
        elif cmd == "approve":
            await self.on_approve(command)
        elif cmd == "decline":
            gate_id = str(command.get("gate_id") or "")
            if self.pending and self.pending.get("gate_id") == gate_id:
                label = self.pending["label"]
                await self.reply(
                    self.pending["call_id"],
                    {
                        "decision": "refuse",
                        "reason": "The person said not now. Do not try another way.",
                    },
                )
                self.refused.append(f"You said not now: {label}")
                self.say(f"You said not now: {label}", kind="refused")
                self.pending = None
                self.set_state(WORKING, "Working…")
        return True

    async def on_approve(self, command: dict[str, Any]) -> None:
        gate_id = str(command.get("gate_id") or "")
        pending = self.pending
        if not pending or pending.get("gate_id") != gate_id or pending.get("approved"):
            await channel.push_outcome(
                gate_id,
                {
                    "ok": False,
                    "note": "The browser is no longer at that step; nothing was pressed.",
                },
            )
            return
        if str(command.get("digest") or "") != pending.get("digest"):
            await channel.push_outcome(
                gate_id,
                {
                    "ok": False,
                    "note": "That approval was for a different step; nothing was pressed.",
                },
            )
            return
        pending["approved"] = True
        self.dirty = True
        await self.reply(
            pending["call_id"],
            {"decision": "allow", "gate_id": gate_id, "digest": pending["digest"]},
        )
        self.set_state(WORKING, f"Doing what you approved: {pending['label']}")

    # -- the end --

    async def _export_cookies(self) -> list[dict[str, Any]]:
        assert self.handle is not None
        ref = uuid.uuid4().hex[:8]
        try:
            await self.driver.send(self.handle, {"cmd": "export_cookies", "ref": ref})
        except Exception:  # noqa: BLE001
            return []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = await self.driver.next(self.handle, wait=1.0)
            if message is None:
                continue
            if message.get("type") == "cookies" and message.get("ref") == ref:
                return [
                    c for c in (message.get("cookies") or []) if isinstance(c, dict)
                ]
            if message.get("type") == "exited":
                break
        return []

    async def finish(self, state: str, note: str) -> None:
        if self.state in TERMINAL:
            return
        kept: list[str] = []
        if self.keep and self.handle is not None:
            if not cookies.can_keep():
                self.say(
                    "Your login could not be kept: this server has no encryption key.",
                    kind="warning",
                )
            else:
                exported = await self._export_cookies()
                for site in self.keep:
                    try:
                        count = await cookies.save(
                            organization_id=self.row.organization_id,
                            user_id=self.row.user_id,
                            site=site,
                            cookies=exported,
                        )
                    except cookies.LoginsUnavailable as exc:
                        self.say(str(exc), kind="warning")
                        break
                    if count:
                        kept.append(site)
                        self.say(
                            f"Kept you signed in to {site} for next time.",
                            kind="person",
                        )
        if self.handle is not None:
            try:
                await self.driver.send(self.handle, {"cmd": "stop"})
            except Exception:  # noqa: BLE001
                pass
            await self.driver.stop(self.handle)
        await self._cancel_pending()
        self.state, self.note = state, note
        self.used["minutes"] = int((time.monotonic() - self.started) // 60)
        summary = self.result or note
        receipt = {
            "state": state,
            "summary": summary,
            "note": note,
            "done": self.pressed,
            "refused": self.refused[-20:],
            "links": self.links
            or ([self.current_url] if self.current_url.startswith("http") else []),
            "used": dict(self.used),
            "limits": dict(self.limits),
            "kept_logins": kept,
            "used_logins": self.loaded_logins,
        }
        self.say(note, kind="end")
        await db_client.update_browser_session(
            self.row.id,
            state=state,
            state_note=note[:500],
            steps=self.steps,
            used=dict(self.used),
            pending=None,
            receipt=receipt,
            keep_login_sites=self.keep,
            ended_at=_now(),
            driver_handle=None,
        )
        await channel.drop(self.row.session_uuid)
        await self._tell_thread(state, summary)

    async def _cancel_pending(self) -> None:
        if not self.pending:
            return
        from api.services.workflow import actions

        event = await db_client.get_agent_event(
            self.pending["event_id"], organization_id=self.row.organization_id
        )
        if event is not None:
            payload = dict(event.payload or {})
            if payload.get("state") == actions.PROPOSED:
                payload["state"] = actions.CANCELLED
                payload["cancelled"] = {
                    "by": None,
                    "at": _now().isoformat(),
                    "note": "The browser closed first.",
                }
                await db_client.transition_agent_event_payload(
                    event.id,
                    organization_id=self.row.organization_id,
                    from_state=actions.PROPOSED,
                    payload=payload,
                )
        await channel.push_outcome(
            self.pending["gate_id"],
            {
                "ok": False,
                "note": "The browser closed before it could do that; nothing was pressed.",
            },
        )
        self.pending = None

    async def _tell_thread(self, state: str, summary: str) -> None:
        lead = {
            DONE: "The browser finished",
            FAILED: "The browser could not finish",
            STOPPED: "The browser stopped",
            LIMIT: "The browser stopped at its limit",
        }.get(state, "The browser stopped")
        body = f"{lead}: {summary}" if summary else f"{lead}."
        await agent_timeline.record(
            organization_id=self.row.organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary=body[:500],
            payload={
                "body": body,
                "from": "Decibyl",
                "browser_session": self.row.session_uuid,
            },
            in_channel=False,
            thread_id=self.row.thread_id,
        )


def _spec(
    run: _Run, rules: list[sites.Rule], loaded: list[dict[str, Any]]
) -> dict[str, Any]:
    row = run.row
    start_url = run.start_url or (f"https://{row.sites[0]}/" if row.sites else "")
    spec: dict[str, Any] = {
        "session": row.session_uuid,
        "task": row.task,
        "sites": list(row.sites or []),
        "start_url": start_url,
        "rules": sites.effective_list(rules),
        "system": system_rules(list(row.sites or []), list(row.allowed_verbs or [])),
        "cookies": loaded,
        "limits": dict(run.limits),
        "max_steps": run.limits["steps"],
    }
    if (
        constants.DEPLOYMENT_MODE in ("oss", "test", "dev")
        and run.driver.name != "sandbox"
    ):
        # Development only: lets a local box open a page this machine serves.
        hosts = [
            h for h in (constants.BROWSER_TEST_HOSTS or "").split(",") if h.strip()
        ]
        if hosts:
            spec["test_hosts"] = [h.strip() for h in hosts]
    return spec


async def run(session_uuid: str, start_url: str = "") -> None:
    """The job: one box from open to close. Never raises."""
    row = await db_client.get_browser_session_for_worker(session_uuid)
    if row is None:
        logger.warning("Browser session {} vanished before it ran", session_uuid)
        return
    if row.state != STARTING or not await db_client.claim_browser_session(row.id):
        # A retried job: the first one owns the box. Never two browsers.
        return
    try:
        driver = drivers.get()
    except drivers.BrowserUnavailable as exc:
        await db_client.update_browser_session(
            row.id,
            state=FAILED,
            state_note=str(exc)[:500],
            ended_at=_now(),
            receipt={
                "state": FAILED,
                "summary": str(exc),
                "note": str(exc),
                "done": [],
                "refused": [],
                "links": [],
            },
        )
        return
    current = _Run(row, driver, start_url)
    try:
        await _drive(current)
    except Exception as exc:  # noqa: BLE001 - the panel must say something
        logger.exception("Browser session {} failed: {}", session_uuid, exc)
        await current.finish(
            FAILED, "Something went wrong on our side. This is us, not you."
        )


async def _drive(run: _Run) -> None:
    rules = await sites.staff_rules()
    run.task.rules = rules
    loaded, used_sites = await cookies.load(
        organization_id=run.row.organization_id,
        user_id=run.row.user_id,
        task_sites=list(run.row.sites or []),
    )
    run.loaded_logins = used_sites
    for site in used_sites:
        run.say(f"Signed in with your saved login for {site}.", kind="person")
    try:
        run.handle = await run.driver.start(_spec(run, rules, loaded))
    except drivers.BrowserUnavailable as exc:
        await run.finish(FAILED, str(exc))
        return
    await db_client.update_browser_session(
        run.row.id, started_at=_now(), driver_handle=str(run.handle)[:64]
    )
    deadline = run.started + run.limits["minutes"] * 60
    while run.state not in TERMINAL:
        if time.monotonic() >= deadline:
            await run.finish(
                LIMIT, f"Stopped at the time limit ({run.limits['minutes']} minutes)."
            )
            break
        if run.wait_until is not None and time.monotonic() >= run.wait_until:
            await run.finish(
                STOPPED,
                f"Nobody answered for {constants.BROWSER_WAIT_MINUTES} minutes, so it stopped.",
            )
            break
        for command in await channel.pop_commands(run.row.session_uuid):
            if not await run.on_command(command):
                break
        if run.state in TERMINAL:
            break
        message = await run.driver.next(run.handle, wait=POLL_SECONDS)
        if message is not None and not await run.on_message(message):
            break
        await run.save()


# --- the person's presses, from a route ---------------------------------------------


class CommandRefused(ValueError):
    """The press does not fit the browser's state; the message says why."""


INPUT_KINDS = ("click", "type", "key", "scroll")
KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Backspace",
        "Delete",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Space",
    }
)


async def person_command(row: Any, command: dict[str, Any]) -> None:
    """Pass a press from the person to the job running their browser. The
    row is already theirs (the route read it by organisation and person)."""
    cmd = command.get("cmd")
    if row.state in TERMINAL:
        raise CommandRefused("This browser has closed.")
    if cmd == "takeover" and row.state == TAKEN_OVER:
        raise CommandRefused("You already have the browser.")
    if cmd in ("input", "handback") and row.state != TAKEN_OVER:
        raise CommandRefused("Take over first.")
    if cmd == "input":
        kind = command.get("kind")
        if kind not in INPUT_KINDS:
            raise CommandRefused("That is not something the browser can be sent.")
        if kind == "key" and command.get("key") not in KEYS:
            raise CommandRefused("That key cannot be sent.")
    await channel.push_command(row.session_uuid, command)


# --- the approval card's half ------------------------------------------------------


async def approved(
    payload: dict[str, Any], *, organization_id: int, timeout: float = 90.0
) -> dict[str, Any]:
    """A person confirmed a browser step's card: hand it to the running
    session and wait for what happened. Returns {ok, note} or {unknown}."""
    args = payload.get("args") or {}
    session_uuid = str(args.get("session_uuid") or "")
    row = await db_client.get_browser_session_for_worker(session_uuid)
    if row is None or row.organization_id != organization_id:
        return {"ok": False, "note": "That browser is not here."}
    confirmed_by = int(((payload.get("confirmed") or {}).get("by")) or 0)
    if confirmed_by != int(row.user_id):
        # Only the person whose browser it is can approve what it does there.
        return {
            "ok": False,
            "note": "Only the person who asked can approve this browser's steps.",
        }
    if row.state in TERMINAL:
        return {
            "ok": False,
            "note": "The browser had already closed; nothing was pressed.",
        }
    gate_id = str(args.get("gate_id") or "")
    await channel.push_command(
        session_uuid,
        {"cmd": "approve", "gate_id": gate_id, "digest": args.get("digest")},
    )
    outcome = await channel.wait_outcome(gate_id, timeout=timeout)
    if outcome is None:
        return {"unknown": True}
    return outcome


async def declined(payload: dict[str, Any]) -> None:
    args = payload.get("args") or {}
    session_uuid = str(args.get("session_uuid") or "")
    if session_uuid:
        await channel.push_command(
            session_uuid, {"cmd": "decline", "gate_id": str(args.get("gate_id") or "")}
        )
