"""Decibyl's private browser: what runs inside one browser box.

browser-use drives a headless Chromium for one person's task. This file is
the harness around it, and it holds nothing worth stealing: no key, no
database, no other person's anything. It talks to the api in lines -- the
protocol in ``api/services/browser/drivers.py`` -- over stdin and stdout,
which the sandbox service relays.

What it changes about browser-use, and why:

- **The model is called by the api.** ``BridgedAnthropic`` is browser-use's
  own ``ChatAnthropic`` with its one network call replaced by a request line.
  The api adds the key, checks the limits, counts the cost and, when the page
  is talking to the assistant, says so in the prompt.
- **Every step is asked about first.** ``GatedTools.act`` describes the step
  -- the action, the page, the element as the DOM has it (its words, its
  form, the form's filled fields with secrets masked) -- and waits for the
  api's answer: allow, refuse (the model is told why), or, for something
  consequential the person asked for, an approval card. An approved step is
  pressed once, and only if the element is still the one that was approved.
- **Every request goes through the box's proxy** (``netguard.py``), which
  refuses private addresses, names that resolve to them and denied sites.
- **The person can take over**: clicks, keys and scrolls arrive as commands
  and are dispatched to the page; the agent waits until they hand back. A
  CAPTCHA pauses it and asks for them.
- **Only cookies leave**, and only when the api asks (the person said "keep
  me signed in").
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from typing import Any
from urllib.parse import quote_plus

SENTINEL = "@@decibyl-tool@@"

# The protocol owns stdout. Libraries that print (browser-use does, on
# pause) are sent to stderr, which the sandbox keeps as the box's log.
_PROTO = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr

import netguard  # noqa: E402  (beside this file in the image)

DESCRIBE_JS = """
function () {
  const el = this;
  const secret = (f) => {
    const words = [f.type, f.name, f.id, f.autocomplete, f.getAttribute('aria-label'),
                   f.placeholder].join(' ').toLowerCase();
    return f.type === 'password' || /pass|pin|otp|one-time|cvv|cvc|csc|card|cc-/.test(words);
  };
  const form = el.form || (el.closest ? el.closest('form') : null);
  const fields = {};
  let searchOnly = !!form;
  if (form) {
    for (const f of Array.from(form.elements || [])) {
      if (!f.name && !f.id) continue;
      const t = (f.type || '').toLowerCase();
      if (['submit', 'button', 'hidden', 'image', 'reset'].includes(t)) continue;
      const key = (f.name || f.id).slice(0, 80);
      let value = (t === 'checkbox' || t === 'radio') ? (f.checked ? (f.value || 'on') : '')
                : String(f.value || '');
      if (secret(f) && value) value = '••••••';
      fields[key] = value.slice(0, 200);
      if (t !== 'search' && !/^(q|query|search|s|keyword)$/i.test(key)) searchOnly = false;
    }
  }
  const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
  return {
    tag: (el.tagName || '').toLowerCase(),
    type: (el.getAttribute('type') || '').toLowerCase(),
    text: text.replace(/\\s+/g, ' ').slice(0, 200),
    name: el.getAttribute('name') || '',
    id: el.id || '',
    role: el.getAttribute('role') || '',
    aria_label: el.getAttribute('aria-label') || '',
    placeholder: el.getAttribute('placeholder') || '',
    autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase(),
    href: el.href ? String(el.href) : '',
    in_form: !!form,
    form_action: form ? String(form.action || '') : '',
    form_method: form ? String(form.method || '') : '',
    form_fields: fields,
    form_is_search: searchOnly && Object.keys(fields).length > 0,
  };
}
"""

CAPTCHA_JS = """
(() => {
  const frames = Array.from(document.querySelectorAll('iframe')).map(f => f.src || '');
  if (frames.some(s => /recaptcha|hcaptcha|challenges\\.cloudflare\\.com|turnstile|arkoselabs|funcaptcha/i.test(s))) return true;
  const t = (document.body && document.body.innerText || '').slice(0, 5000).toLowerCase();
  return /verify (that )?you are (a )?human|i'm not a robot|are you a robot|complete the security check|press and hold/.test(t);
})()
"""

SEARCH_URLS = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
}


def where(url: str) -> str:
    """An address without its query or fragment: host and path."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    if not parts.netloc:
        return url or ""
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def emit(message: dict[str, Any]) -> None:
    _PROTO.write(SENTINEL + json.dumps(message, default=str) + "\n")
    _PROTO.flush()


class Line:
    """Requests to the api and its answers; commands from it."""

    def __init__(self) -> None:
        self._n = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self.commands: asyncio.Queue = asyncio.Queue()
        self.closed = asyncio.Event()

    async def ask(self, kind: str, **payload: Any) -> Any:
        self._n += 1
        call_id = self._n
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[call_id] = future
        emit({"type": kind, "id": call_id, **payload})
        return await future

    def cancel_all(self, result: Any) -> None:
        for future in self._waiting.values():
            if not future.done():
                future.set_result(result)
        self._waiting.clear()

    async def read(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=32 * 1024 * 1024)
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        while True:
            raw = await reader.readline()
            if not raw:
                self.closed.set()
                self.cancel_all(
                    {
                        "decision": "refuse",
                        "reason": "closed",
                        "ok": False,
                        "error": "closed",
                    }
                )
                await self.commands.put({"cmd": "stop"})
                return
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if "reply_to" in message:
                future = self._waiting.pop(int(message["reply_to"]), None)
                if future is not None and not future.done():
                    future.set_result(message.get("result"))
            else:
                await self.commands.put(message)


LINE = Line()


# --- the model, through the api ---------------------------------------------------


def bridged_model():
    from anthropic.types import Message
    from browser_use.llm.anthropic.chat import ChatAnthropic
    from browser_use.llm.exceptions import ModelProviderError

    class BridgedAnthropic(ChatAnthropic):
        async def _create_message(self, **params: Any) -> Any:
            body = {
                k: v
                for k, v in params.items()
                if k
                in (
                    "messages",
                    "system",
                    "tools",
                    "tool_choice",
                    "max_tokens",
                    "temperature",
                )
                and type(v).__name__ not in ("Omit", "NotGiven")
            }
            result = await LINE.ask(
                "llm", body=json.loads(json.dumps(body, default=str))
            )
            if not (result or {}).get("ok"):
                raise ModelProviderError(
                    message=str(
                        (result or {}).get("error") or "the model did not answer"
                    ),
                    status_code=503,
                    model=self.name,
                )
            return Message.model_validate(result["response"])

    model = BridgedAnthropic(model="decibyl-bridged", api_key="unused", max_tokens=4096)
    model._verified_api_keys = True
    return model


# --- the browser ----------------------------------------------------------------


class Box:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.session = None
        self.agent = None
        self.taken_over = False
        self.handed_back = asyncio.Event()
        self.handed_back.set()
        self.captcha_seen_at: str = ""
        self.stopping = False

    # -- the page --

    async def _cdp(self):
        return await self.session.get_or_create_cdp_session(target_id=None, focus=False)

    async def evaluate(self, expression: str) -> Any:
        cdp = await self._cdp()
        result = await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expression, "returnByValue": True},
            session_id=cdp.session_id,
        )
        return (result.get("result") or {}).get("value")

    async def page_text(self) -> str:
        try:
            return str(
                await self.evaluate(
                    "(document.body && document.body.innerText || '').slice(0, 6000)"
                )
                or ""
            )
        except Exception:  # noqa: BLE001
            return ""

    async def describe_index(self, index: int) -> dict[str, Any] | None:
        node = await self.session.get_element_by_index(index)
        if node is None:
            return None
        cdp = await self.session.cdp_client_for_node(node)
        resolved = await cdp.cdp_client.send.DOM.resolveNode(
            params={"backendNodeId": node.backend_node_id}, session_id=cdp.session_id
        )
        object_id = (resolved.get("object") or {}).get("objectId")
        if not object_id:
            return None
        described = await cdp.cdp_client.send.Runtime.callFunctionOn(
            params={
                "functionDeclaration": DESCRIBE_JS,
                "objectId": object_id,
                "returnByValue": True,
            },
            session_id=cdp.session_id,
        )
        return (described.get("result") or {}).get("value")

    async def describe_expression(self, expression: str) -> dict[str, Any] | None:
        """The element an expression finds (the focused one, the one at a point)."""
        cdp = await self._cdp()
        found = await cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expression}, session_id=cdp.session_id
        )
        object_id = (found.get("result") or {}).get("objectId")
        if not object_id:
            return None
        described = await cdp.cdp_client.send.Runtime.callFunctionOn(
            params={
                "functionDeclaration": DESCRIBE_JS,
                "objectId": object_id,
                "returnByValue": True,
            },
            session_id=cdp.session_id,
        )
        return (described.get("result") or {}).get("value")

    async def after_note(self) -> str:
        """One line on where an approved press left the page. The page's
        title, or its address without the query -- a query can carry what
        was just typed, and this line goes on the card and the thread."""
        url = await self.current_url()
        try:
            title = (await self.session.get_current_page_title() or "").strip()
        except Exception:  # noqa: BLE001
            title = ""
        if (
            title
            and "?" not in title
            and not title.startswith(("http", url.split("://")[-1][:20]))
        ):
            return f"The page now shows “{title[:120]}”."
        return f"Now on {where(url)}." if url else "Pressed."

    async def current_url(self) -> str:
        try:
            return await self.session.get_current_page_url()
        except Exception:  # noqa: BLE001
            return ""

    async def screenshot(self) -> None:
        try:
            jpeg = await self.session.take_screenshot(format="jpeg", quality=55)
            title = ""
            try:
                title = await self.session.get_current_page_title()
            except Exception:  # noqa: BLE001
                pass
            emit(
                {
                    "type": "screen",
                    "jpeg": base64.b64encode(jpeg).decode("ascii"),
                    "w": 1280,
                    "h": 800,
                    "url": await self.current_url(),
                    "title": title,
                }
            )
        except Exception as exc:  # noqa: BLE001 - a missed frame is not a failure
            print(f"screenshot failed: {exc}", file=sys.stderr)

    # -- Take over --

    async def dispatch(self, command: dict[str, Any]) -> None:
        """A press from the person, while they have the browser."""
        if not self.taken_over:
            return
        cdp = await self._cdp()
        send = cdp.cdp_client.send.Input
        kind = command.get("kind")
        sid = cdp.session_id
        if kind == "click":
            x, y = float(command.get("x") or 0), float(command.get("y") or 0)
            for event in ("mousePressed", "mouseReleased"):
                await send.dispatchMouseEvent(
                    params={
                        "type": event,
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 1,
                    },
                    session_id=sid,
                )
        elif kind == "type":
            await send.insertText(
                params={"text": str(command.get("text") or "")[:500]}, session_id=sid
            )
        elif kind == "key":
            key = str(command.get("key") or "")
            codes = {
                "Enter": 13,
                "Tab": 9,
                "Backspace": 8,
                "Delete": 46,
                "Escape": 27,
                "ArrowUp": 38,
                "ArrowDown": 40,
                "ArrowLeft": 37,
                "ArrowRight": 39,
                "Home": 36,
                "End": 35,
                "PageUp": 33,
                "PageDown": 34,
                "Space": 32,
            }
            name = " " if key == "Space" else key
            for event in ("keyDown", "keyUp"):
                params = {
                    "type": event,
                    "key": name,
                    "windowsVirtualKeyCode": codes.get(key, 0),
                }
                if key == "Enter" and event == "keyDown":
                    params["text"] = "\r"
                await send.dispatchKeyEvent(params=params, session_id=sid)
        elif kind == "scroll":
            await send.dispatchMouseEvent(
                params={
                    "type": "mouseWheel",
                    "x": 640,
                    "y": 400,
                    "deltaX": 0,
                    "deltaY": float(command.get("dy") or 0),
                },
                session_id=sid,
            )
        await asyncio.sleep(0.2)
        await self.screenshot()

    def take_over(self) -> None:
        self.taken_over = True
        self.handed_back.clear()
        if self.agent is not None:
            self.agent.pause()
        emit({"type": "state", "state": "taken_over"})

    def hand_back(self) -> None:
        self.taken_over = False
        self.handed_back.set()
        if self.agent is not None and not self.stopping:
            self.agent.resume()
        emit({"type": "state", "state": "working"})

    async def cookies(self, ref: str) -> None:
        try:
            result = await self.session.cdp_client.send.Storage.getCookies()
            found = result.get("cookies", [])
        except Exception as exc:  # noqa: BLE001
            print(f"cookies failed: {exc}", file=sys.stderr)
            found = []
        emit({"type": "cookies", "ref": ref, "cookies": found})

    async def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        if not cookies:
            return
        allowed = (
            "name",
            "value",
            "domain",
            "path",
            "expires",
            "httpOnly",
            "secure",
            "sameSite",
        )
        clean = [{k: c[k] for k in allowed if k in c} for c in cookies if c.get("name")]
        await self.session.cdp_client.send.Storage.setCookies(params={"cookies": clean})

    # -- loops --

    async def commands(self) -> None:
        while True:
            command = await LINE.commands.get()
            cmd = command.get("cmd")
            try:
                if cmd == "takeover":
                    self.take_over()
                elif cmd == "handback":
                    self.hand_back()
                elif cmd == "input":
                    await self.dispatch(command)
                elif cmd == "export_cookies":
                    await self.cookies(str(command.get("ref") or ""))
                elif cmd == "stop":
                    self.stopping = True
                    if self.agent is not None:
                        self.agent.stop()
                    self.handed_back.set()
                    LINE.cancel_all(
                        {
                            "decision": "refuse",
                            "reason": "stopped",
                            "ok": False,
                            "error": "stopped",
                        }
                    )
                    return
            except Exception as exc:  # noqa: BLE001 - one bad press is not the end
                print(f"command {cmd} failed: {exc}", file=sys.stderr)

    async def watch(self) -> None:
        """Screens for the panel, and the CAPTCHA check."""
        while not self.stopping:
            await asyncio.sleep(0.7 if self.taken_over else 1.5)
            if self.session is None:
                continue
            await self.screenshot()
            if self.taken_over:
                continue
            if await self.captcha_here() and self.agent is not None:
                self.agent.pause()

    async def captcha_here(self) -> bool:
        """Whether the page is a CAPTCHA the person has not been asked about
        yet; if so, say so and stop handing steps over until they hand back."""
        try:
            blocked = bool(await self.evaluate(CAPTCHA_JS))
        except Exception:  # noqa: BLE001
            blocked = False
        url = await self.current_url()
        if not blocked or url == self.captcha_seen_at:
            return False
        self.captcha_seen_at = url
        self.handed_back.clear()
        emit(
            {
                "type": "state",
                "state": "captcha",
                "note": "The page asks to prove you are human.",
            }
        )
        return True


def gated_tools(box: Box):
    from browser_use import Tools
    from browser_use.agent.views import ActionResult

    class GatedTools(Tools):
        async def act(self, action, browser_session, *args, **kwargs):
            for name, params in action.model_dump(exclude_unset=True).items():
                if params is None:
                    continue
                if name == "ask_person":
                    break
                # Checked here, before every step, and not only on the
                # screenshot timer: a quick step must not slip past a
                # CAPTCHA the person has not seen.
                if not box.taken_over and await box.captcha_here():
                    await box.handed_back.wait()
                if box.taken_over:
                    await box.handed_back.wait()
                request = await box_request(box, name, params)
                verdict = await LINE.ask("gate", gate=request) or {}
                if verdict.get("decision") != "allow":
                    reason = str(verdict.get("reason") or "refused")
                    return ActionResult(
                        error=f"Not done -- Decibyl refused this step: {reason}"
                    )
                if verdict.get("gate_id"):
                    # Approved on a card. Press it only if it is still the
                    # button the person approved.
                    again = await box_request(box, name, params)
                    if again.get("element") != request.get("element") or again.get(
                        "url"
                    ) != request.get("url"):
                        emit(
                            {
                                "type": "gate_result",
                                "gate_id": verdict["gate_id"],
                                "ok": False,
                                "note": "The page changed before it could be pressed; nothing was pressed.",
                                "url": await box.current_url(),
                            }
                        )
                        return ActionResult(
                            error="The page changed; the approved step was not taken."
                        )
                    result = await super().act(action, browser_session, *args, **kwargs)
                    await asyncio.sleep(1.0)
                    error = getattr(result, "error", None)
                    emit(
                        {
                            "type": "gate_result",
                            "gate_id": verdict["gate_id"],
                            "ok": not error,
                            "note": (await box.after_note())
                            if not error
                            else str(error)[:200],
                            "url": where(await box.current_url()),
                        }
                    )
                    return result
            return await super().act(action, browser_session, *args, **kwargs)

    tools = GatedTools(
        exclude_actions=[
            "evaluate",
            "upload_file",
            "write_file",
            "replace_file",
            "read_file",
            "save_as_pdf",
            "screenshot",
        ]
    )

    @tools.registry.action(
        "Ask the person to take over the browser for something only they may do: "
        "sign in, type a password or one-time code, solve a CAPTCHA, enter card "
        "details. Say why in one line. Waits until they hand the browser back."
    )
    async def ask_person(reason: str):
        box.handed_back.clear()
        emit({"type": "state", "state": "needs_person", "note": reason[:200]})
        await box.handed_back.wait()
        return ActionResult(
            extracted_content="The person has handed the browser back. Look at the page again."
        )

    return tools


async def box_request(box: Box, name: str, params: dict[str, Any]) -> dict[str, Any]:
    """The gate's description of one step, from the page itself."""
    request: dict[str, Any] = {"action": name, "url": await box.current_url()}
    try:
        if name == "navigate":
            request["target_url"] = str(params.get("url") or "")
        elif name == "search":
            engine = str(params.get("engine") or "duckduckgo").lower()
            template = SEARCH_URLS.get(engine, SEARCH_URLS["duckduckgo"])
            request["target_url"] = template.format(
                q=quote_plus(str(params.get("query") or ""))
            )
        elif name in ("click", "input", "select_dropdown", "dropdown_options"):
            index = params.get("index")
            if index is not None:
                request["element"] = await box.describe_index(int(index))
            elif params.get("coordinate_x") is not None:
                x, y = int(params["coordinate_x"]), int(params["coordinate_y"] or 0)
                request["element"] = await box.describe_expression(
                    f"document.elementFromPoint({x}, {y})"
                )
            if name == "input":
                request["text"] = str(params.get("text") or "")
            if name == "select_dropdown":
                request["text"] = str(params.get("text") or "")
        elif name == "send_keys":
            request["keys"] = str(params.get("keys") or "")
            request["element"] = await box.describe_expression("document.activeElement")
    except Exception as exc:  # noqa: BLE001 - an undescribed step is asked about as such
        print(f"describe failed: {exc}", file=sys.stderr)
    request["page_text"] = await box.page_text()
    return request


async def main() -> None:
    reader = asyncio.create_task(LINE.read())
    first = await LINE.commands.get()
    if first.get("cmd") != "start":
        emit({"type": "done", "ok": False, "error": "no start"})
        return
    spec = first.get("spec") or {}
    box = Box(spec)

    guard = netguard.Guard(spec.get("rules") or [], spec.get("test_hosts") or [])
    proxy = await netguard.serve(guard)
    port = proxy.sockets[0].getsockname()[1]

    from browser_use import Agent, BrowserProfile, BrowserSession
    from browser_use.browser.profile import ProxySettings

    profile = BrowserProfile(
        headless=True,
        executable_path=os.environ.get("CHROME_PATH") or None,
        user_data_dir="/work/profile" if os.path.isdir("/work") else None,
        downloads_path="/work/downloads" if os.path.isdir("/work") else None,
        proxy=ProxySettings(server=f"http://127.0.0.1:{port}", bypass="<-loopback>"),
        args=[
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--disable-features=DnsOverHttps",
        ],
        chromium_sandbox=False,
        enable_default_extensions=False,
        viewport={"width": 1280, "height": 800},
        window_size={"width": 1280, "height": 800},
        keep_alive=True,
    )
    box.session = BrowserSession(browser_profile=profile)
    await box.session.start()
    try:
        await box.set_cookies(list(spec.get("cookies") or []))
    except Exception as exc:  # noqa: BLE001
        print(f"could not set cookies: {exc}", file=sys.stderr)

    tools = gated_tools(box)
    start_url = str(spec.get("start_url") or "")
    initial = (
        [{"navigate": {"url": start_url, "new_tab": False}}] if start_url else None
    )
    wait_minutes = int((spec.get("limits") or {}).get("minutes") or 10)

    async def on_step(state, output, n) -> None:
        emit(
            {
                "type": "step",
                "n": n,
                "goal": str(getattr(output, "next_goal", "") or "")[:300],
                "evaluation": str(
                    getattr(output, "evaluation_previous_goal", "") or ""
                )[:300],
                "url": getattr(state, "url", ""),
            }
        )

    box.agent = Agent(
        task=str(spec.get("task") or ""),
        llm=bridged_model(),
        browser_session=box.session,
        tools=tools,
        extend_system_message=str(spec.get("system") or ""),
        use_vision=True,
        max_actions_per_step=3,
        max_failures=3,
        initial_actions=initial,
        directly_open_url=False,
        register_new_step_callback=on_step,
        step_timeout=wait_minutes * 60 + 60,
    )
    emit({"type": "hello"})
    commands = asyncio.create_task(box.commands())
    watcher = asyncio.create_task(box.watch())
    await box.screenshot()
    started = time.monotonic()
    try:
        history = await box.agent.run(max_steps=int(spec.get("max_steps") or 25))
        result = history.final_result() or ""
        links = [u for u in history.urls() if u and u.startswith("http")]
        ok = bool(history.is_done()) and history.is_successful() is not False
        errors = [e for e in history.errors() if e]
        emit(
            {
                "type": "done",
                "ok": ok,
                "result": result,
                "links": list(dict.fromkeys(links))[-5:],
                "error": "" if ok else (errors[-1] if errors else "It did not finish."),
                "seconds": int(time.monotonic() - started),
            }
        )
    except Exception as exc:  # noqa: BLE001 - said, then the box ends
        emit({"type": "done", "ok": False, "error": f"The browser failed: {exc}"[:300]})
    # Stay for the api's last asks (cookies, stop), then go.
    try:
        await asyncio.wait_for(commands, timeout=60)
    except asyncio.TimeoutError:
        pass
    box.stopping = True
    watcher.cancel()
    reader.cancel()
    try:
        await box.session.kill()
    except Exception:  # noqa: BLE001
        pass
    proxy.close()


if __name__ == "__main__":
    asyncio.run(main())
