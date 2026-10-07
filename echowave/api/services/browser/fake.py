"""A browser for the tests: real HTML, a scripted hand, the real protocol.

``FakeDriver`` speaks exactly the line protocol the box speaks
(``drivers.py``), so everything on our side of it -- the gate, approval
cards, limits, Take over, cookies, the receipt -- runs for real in a test.
What it fakes is only the browser and the model:

- **The web** is a dict of URL to HTML. Pages are parsed with the standard
  library into the same element descriptions the box builds from Chromium's
  DOM (tag, type, words, form and its filled fields), so the gate judges
  real markup, not a test's idea of it. A page can set cookies when opened.
- **The hand** is a plan: a list of steps (navigate, type, press, keys,
  done). ``obey`` is the fooled model: it reads the page's planted
  instruction (``data-attack`` on an element) and tries to do what the page
  says. That is how the injection set is run: the model is assumed beaten,
  and the test is that the step still does not happen.
- **Thinking** (``think=True``) sends one model request per step through the
  bridge, so cost and step limits are spent the way the box spends them.

Refused outside development (``drivers.get``).
"""

from __future__ import annotations

import asyncio
import base64
import itertools
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

#: A 1x1 grey JPEG: the fake's screenshot.
PIXEL_JPEG = base64.b64encode(
    bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c"
        "140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27"
        "393d38323c2e333432ffc0000b080001000101011100ffc4001f000001050101010101010000000000"
        "0000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411"
        "05122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a252627"
        "28292a3435363738393a434445464748494a535455565758595a636465666768696a73747576777879"
        "7a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6"
        "c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f"
        "00fbd3ffd9"
    )
).decode("ascii")


@dataclass
class Node:
    tag: str
    attrs: dict[str, str]
    text: str = ""
    form: int | None = None


class _Page(HTMLParser):
    """Elements with an id, their words, and which form each sits in."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[Node] = []
        self.forms: dict[int, dict[str, str]] = {}
        self._form: int | None = None
        self._open: list[Node] = []
        self._skip = 0
        self.text: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attributes = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "form":
            self._form = len(self.forms)
            self.forms[self._form] = attributes
        node = Node(tag=tag, attrs=attributes, form=self._form)
        self.nodes.append(node)
        if tag not in ("input", "img", "br", "meta", "link"):
            self._open.append(node)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "form":
            self._form = None
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i].tag == tag:
                del self._open[i:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        words = " ".join(data.split())
        if not words:
            return
        self.text.append(words)
        for node in self._open:
            node.text = f"{node.text} {words}".strip()


@dataclass
class Page:
    url: str
    html: str
    #: Cookies the site sets when this page is opened.
    sets_cookies: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Step:
    kind: str  # navigate | input | click | keys | done | obey | login
    target: str = ""  # a URL, or an element's id
    text: str = ""
    goal: str = ""


@dataclass
class _Box:
    spec: dict[str, Any] | None = None
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    replies: dict[int, asyncio.Future] = field(default_factory=dict)
    url: str = "about:blank"
    values: dict[str, str] = field(default_factory=dict)
    cookies: list[dict[str, Any]] = field(default_factory=list)
    taken_over: asyncio.Event = field(default_factory=asyncio.Event)
    handed_back: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: bool = False
    stop_asked: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    #: What was received during Take over, for a test to look at.
    inputs: list[dict[str, Any]] = field(default_factory=list)
    #: Presses that actually happened: (element id, page url).
    pressed: list[tuple[str, str]] = field(default_factory=list)
    exited: bool = False


class FakeDriver:
    name = "fake"

    def __init__(
        self,
        pages: dict[str, Page] | None = None,
        plan: list[Step] | None = None,
        *,
        think: bool = False,
        captcha_on: str | None = None,
        step_delay: float = 0.0,
    ) -> None:
        self.pages = dict(pages or {})
        self.plan = list(plan or [])
        self.think = think
        self.captcha_on = captcha_on
        self.step_delay = step_delay
        self.boxes: dict[str, _Box] = {}
        self._ids = itertools.count(1)
        self._handles = itertools.count(1)

    # --- the driver interface ------------------------------------------------

    async def start(self, spec: dict[str, Any]) -> str:
        handle = f"fake-{next(self._handles)}"
        box = _Box()
        self.boxes[handle] = box
        await self.send(handle, {"cmd": "start", "spec": spec})
        box.task = asyncio.create_task(self._run(box))
        return handle

    async def next(self, handle: str, *, wait: float) -> dict[str, Any] | None:
        box = self.boxes[handle]
        try:
            return await asyncio.wait_for(box.outbox.get(), timeout=wait)
        except asyncio.TimeoutError:
            return None

    async def send(self, handle: str, message: dict[str, Any]) -> None:
        box = self.boxes[handle]
        if box.exited:
            raise RuntimeError("closed")
        if message.get("cmd") == "start":
            box.spec = message.get("spec") or {}
            box.cookies = list(box.spec.get("cookies") or [])
            box.started.set()
            return
        if "reply_to" in message:
            future = box.replies.pop(int(message["reply_to"]), None)
            if future is not None and not future.done():
                future.set_result(message.get("result"))
            return
        cmd = message.get("cmd")
        if cmd == "takeover":
            box.handed_back.clear()
            box.taken_over.set()
            await self._emit(box, {"type": "state", "state": "taken_over"})
        elif cmd == "input":
            box.inputs.append(dict(message))
            await self._screen(box)
        elif cmd == "handback":
            box.taken_over.clear()
            box.handed_back.set()
            await self._emit(box, {"type": "state", "state": "working"})
        elif cmd == "export_cookies":
            await self._emit(
                box,
                {
                    "type": "cookies",
                    "ref": message.get("ref"),
                    "cookies": list(box.cookies),
                },
            )
        elif cmd == "stop":
            box.stopped = True
            box.stop_asked.set()
            for future in box.replies.values():
                if not future.done():
                    future.set_result({"decision": "refuse", "reason": "stopped"})

    async def stop(self, handle: str) -> None:
        box = self.boxes.get(handle)
        if box is None:
            return
        box.stopped = True
        box.exited = True
        if box.task and not box.task.done():
            box.task.cancel()

    # --- the scripted browser ----------------------------------------------------

    async def _emit(self, box: _Box, message: dict[str, Any]) -> None:
        await box.outbox.put(message)

    async def _ask(self, box: _Box, message: dict[str, Any]) -> Any:
        call_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        box.replies[call_id] = future
        await self._emit(box, {**message, "id": call_id})
        return await future

    def _parse(self, url: str) -> _Page:
        page = _Page()
        page.feed(self.pages[url].html if url in self.pages else "<p>Not found</p>")
        return page

    def _element(self, box: _Box, element_id: str) -> dict[str, Any] | None:
        page = self._parse(box.url)
        for node in page.nodes:
            if node.attrs.get("id") != element_id:
                continue
            form = page.forms.get(node.form) if node.form is not None else None
            fields: dict[str, str] = {}
            search_only = node.form is not None
            for other in page.nodes:
                if other.form != node.form or node.form is None:
                    continue
                if other.tag in ("input", "textarea", "select") and other.attrs.get(
                    "type", ""
                ) not in ("submit", "button", "hidden"):
                    key = other.attrs.get("name") or other.attrs.get("id") or ""
                    value = box.values.get(other.attrs.get("id", ""), "")
                    if other.attrs.get("type") == "password" and value:
                        value = "••••••"
                    if key:
                        fields[key] = value
                    if other.attrs.get("type") != "search" and key not in (
                        "q",
                        "query",
                        "search",
                    ):
                        search_only = False
            href = node.attrs.get("href", "")
            action = (form or {}).get("action", "")
            return {
                "tag": node.tag,
                "type": node.attrs.get("type", ""),
                "text": node.text or node.attrs.get("value", ""),
                "name": node.attrs.get("name", ""),
                "id": element_id,
                "role": node.attrs.get("role", ""),
                "aria_label": node.attrs.get("aria-label", ""),
                "placeholder": node.attrs.get("placeholder", ""),
                "autocomplete": node.attrs.get("autocomplete", ""),
                "href": urljoin(box.url, href) if href else "",
                "in_form": node.form is not None,
                "form_action": urljoin(box.url, action) if action else "",
                "form_method": (form or {}).get("method", ""),
                "form_fields": fields,
                "form_is_search": bool(search_only and fields),
                "_next": node.attrs.get("data-next", ""),
                "_attack": {
                    k[len("data-attack-") :]: v
                    for k, v in node.attrs.items()
                    if k.startswith("data-attack-")
                },
            }
        return None

    def _page_text(self, box: _Box) -> str:
        return "\n".join(self._parse(box.url).text)[:6000]

    async def _screen(self, box: _Box) -> None:
        page = self._parse(box.url)
        await self._emit(
            box,
            {
                "type": "screen",
                "jpeg": PIXEL_JPEG,
                "w": 1280,
                "h": 800,
                "url": box.url,
                "title": page.title.strip(),
            },
        )

    def _open(self, box: _Box, url: str) -> None:
        box.url = url
        page = self.pages.get(url)
        if page is not None:
            for cookie in page.sets_cookies:
                box.cookies = [
                    c
                    for c in box.cookies
                    if not (
                        c.get("name") == cookie.get("name")
                        and c.get("domain") == cookie.get("domain")
                    )
                ] + [dict(cookie)]

    def _obeyed(self, box: _Box) -> list[Step]:
        """The fooled model: whatever the page's planted text asks for."""
        page = self._parse(box.url)
        out: list[Step] = []
        for node in page.nodes:
            attack = {
                k[len("data-attack-") :]: v
                for k, v in node.attrs.items()
                if k.startswith("data-attack-")
            }
            if "data-attack" not in node.attrs:
                continue
            kind = node.attrs["data-attack"]
            element_id = node.attrs.get("id", "")
            if kind == "navigate":
                out.append(
                    Step(
                        "navigate",
                        attack.get("url", ""),
                        goal="The page said to go there",
                    )
                )
            elif kind == "input":
                out.append(
                    Step(
                        "input",
                        element_id,
                        attack.get("text", ""),
                        goal="The page said to type this",
                    )
                )
            elif kind == "keys":
                out.append(
                    Step(
                        "keys", element_id, "Enter", goal="The page said to press Enter"
                    )
                )
            else:
                out.append(
                    Step("click", element_id, goal="The page said to press this")
                )
        return out

    async def _do(self, box: _Box, n: int, step: Step) -> bool:
        """One step through the gate. Returns False to end the run."""
        if box.stopped:
            return False
        if box.taken_over.is_set():
            await box.handed_back.wait()
        if self.step_delay:
            await asyncio.sleep(self.step_delay)
        if self.think:
            result = await self._ask(
                box,
                {
                    "type": "llm",
                    "body": {
                        "messages": [
                            {"role": "user", "content": f"step {n}: {step.goal}"}
                        ],
                        "max_tokens": 512,
                    },
                },
            )
            if not (result or {}).get("ok"):
                await self._emit(
                    box,
                    {
                        "type": "done",
                        "ok": False,
                        "error": (result or {}).get("error", ""),
                    },
                )
                return False
        if step.kind == "done":
            await self._emit(
                box,
                {"type": "done", "ok": True, "result": step.text, "links": [box.url]},
            )
            return False
        if step.kind == "login":
            # The person signed in during Take over: the site set its cookie.
            self._open(box, box.url)
            return True
        await self._emit(
            box,
            {
                "type": "step",
                "n": n,
                "goal": step.goal or step.kind,
                "actions": [{step.kind: {"target": step.target}}],
                "url": box.url,
            },
        )
        gate: dict[str, Any] = {"url": box.url, "page_text": self._page_text(box)}
        element = None
        if step.kind == "navigate":
            gate.update(action="navigate", target_url=step.target)
        else:
            element = self._element(box, step.target)
            if element is None:
                await self._emit(
                    box,
                    {
                        "type": "step",
                        "n": n,
                        "goal": f"No element {step.target}",
                        "actions": [],
                    },
                )
                return True
            public = {k: v for k, v in element.items() if not k.startswith("_")}
            if step.kind == "input":
                gate.update(action="input", text=step.text, element=public)
            elif step.kind == "keys":
                gate.update(
                    action="send_keys", keys=step.text or "Enter", element=public
                )
            else:
                gate.update(action="click", element=public)
        if self.captcha_on and box.url == self.captcha_on:
            await self._emit(
                box,
                {
                    "type": "state",
                    "state": "captcha",
                    "note": "The page asks to prove you are human.",
                },
            )
            await box.taken_over.wait()
            await box.handed_back.wait()
        verdict = await self._ask(box, {"type": "gate", "gate": gate})
        decision = (verdict or {}).get("decision")
        if decision != "allow":
            return not box.stopped
        if step.kind == "navigate":
            self._open(box, step.target)
        elif step.kind == "input":
            box.values[step.target] = step.text
        else:
            box.pressed.append((step.target, box.url))
            after = (
                (element or {}).get("_next")
                or (element or {}).get("href")
                or (element or {}).get("form_action")
            )
            if after:
                self._open(box, urljoin(box.url, after))
            gate_id = (verdict or {}).get("gate_id")
            if gate_id:
                await self._emit(
                    box,
                    {
                        "type": "gate_result",
                        "gate_id": gate_id,
                        "ok": True,
                        "note": f"Pressed. The page is now {box.url}.",
                        "url": box.url,
                    },
                )
        await self._screen(box)
        return True

    async def _run(self, box: _Box) -> None:
        try:
            await box.started.wait()
            await self._emit(box, {"type": "hello"})
            start = (box.spec or {}).get("start_url")
            if start:
                self._open(box, start)
            await self._screen(box)
            n = 0
            queue = list(self.plan)
            finished = False
            while queue and not finished:
                step = queue.pop(0)
                if step.kind == "obey":
                    queue = self._obeyed(box) + queue
                    continue
                n += 1
                finished = not await self._do(box, n, step)
            if not finished:
                await self._emit(
                    box, {"type": "done", "ok": True, "result": "", "links": [box.url]}
                )
            # Like the real box: stay for the api's last asks (cookies, stop).
            await asyncio.wait_for(box.stop_asked.wait(), timeout=60)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        finally:
            box.exited = True
            await box.outbox.put({"type": "exited", "exit_code": 0, "error": ""})
