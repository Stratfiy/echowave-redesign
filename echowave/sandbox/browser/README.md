# The browser box (Decibyl's private browser)

One container per person and task. browser-use (MIT,
https://github.com/browser-use/browser-use) drives a headless Chromium for
the task Decibyl was given; this directory is the harness around it. The api
side is `api/services/browser/`; the flag is `decibyl_browser` (off by
default).

## What a box is

Started by the sandbox service (`POST /browsers`, `../server.py`):

- unprivileged (`--user 1000:1000`), every capability dropped,
  `no-new-privileges`, read-only root, a 512 MB tmpfs for the profile and
  downloads that goes with the box, 2 GB and one CPU, 512 processes;
- on `SANDBOX_BROWSER_NETWORK` only -- a bridge the sandbox service creates
  with inter-container traffic switched off (`enable_icc=false`) and nothing
  else attached, so a box reaches the internet and no other container: not
  the api, not the database, not another person's box;
- every request Chromium makes goes through `netguard.py`, the box's own
  proxy, which refuses non-web ports, private and link-local addresses, bare
  hostnames, names that resolve to a private address (and connects to the
  address it checked), and the sites on the deny list the api sends. The
  address rule is the api's `web_tools._is_private`, kept in step by
  `api/tests/test_browser_sites.py`. WebRTC is held to the proxy too.
- **no credentials.** The model is called by the api: `box.py` replaces
  browser-use's `ChatAnthropic._create_message` with a request line, and the
  api adds the key, checks the limits and counts the cost
  (`api/services/browser/bridge.py`).

`egress-guard.sh` adds the same refusal on the host (DOCKER-USER) for the
browser subnet, for anything in the box that is not the browser. Run it as
root once the sandbox has created the network, and from the host's boot
scripts.

## Install

The image is built from `Dockerfile` (Playwright's Python image, which
carries Chromium, plus `browser-use==0.13.11`):

    docker build -t ghcr.io/stratfiy/decibyl-browser-box:latest sandbox/browser

Then, for the sandbox service (`.env` / compose):

    SANDBOX_BROWSER_NETWORK=decibyl_sandbox_browser
    SANDBOX_BROWSER_IMAGE=ghcr.io/stratfiy/decibyl-browser-box:latest
    SANDBOX_BROWSER_SUBNET=172.30.240.0/24      # must match egress-guard.sh
    SANDBOX_MAX_BROWSERS=2

and for the api: `SANDBOX_URL` (already set for scripts), an Anthropic key
under Provider keys (component LLM), `PLATFORM_CREDENTIAL_SECRET` (without
it logins are never kept), and the flag `decibyl_browser` on for the
organisation. `GET /health` on the sandbox reports `browsers.network`
(`ready` once created) and the image state.

## Without Docker (development)

The api's `local` driver runs `box.py` as a subprocess with an interpreter
that has browser-use and a local Chromium. There is no container isolation
(the proxy still applies), and it is refused where `DEPLOYMENT_MODE` says
production:

    python3 -m venv /opt/box && /opt/box/bin/pip install -r sandbox/browser/requirements.txt
    BROWSER_DRIVER=local
    BROWSER_BOX_PYTHON=/opt/box/bin/python
    BROWSER_CHROMIUM_PATH=/path/to/chrome     # e.g. /opt/pw-browsers/chromium-1194/chrome-linux/chrome
    BROWSER_TEST_HOSTS=kirana.test            # dev only: a page this machine serves

The tests use the `fake` driver (`api/services/browser/fake.py`), which
speaks the same protocol over real HTML.

## Upgrading browser-use

The box overrides two things inside browser-use, so read before bumping:

- `Tools.act(action, browser_session, ...)` -- every step is asked about
  here. A new default action with a new name is refused by the api's gate
  until it is added to `gate.READS` or `gate.MOVES` (on purpose: an unknown
  step is refused by name, on the panel).
- `ChatAnthropic._create_message(**params)` -- the one network call. If its
  parameters change, `bridge.PASSED` decides what reaches the vendor.

After a bump, run the local driver once end to end (the session tests do not
exercise browser-use itself).
