# End-to-end suites

Two suites that drive a **running** Decibyl -- usually staging -- the way a
person does: real HTTP, a real browser, two real accounts. Nothing here
imports the app, touches its database or mocks anything. They run after
every staging deploy (`.github/workflows/deploy-staging.yml`, after
`scripts/staging_check.py`), and their results are uploaded as the
`e2e-results-<run>` artifact.

| | Where | What it runs against |
|---|---|---|
| API | `api/` (pytest + httpx) | `STAGING_URL` (default `http://127.0.0.1:8000`) |
| Browser | `browser/` (Playwright, Chromium, one worker) | `STAGING_UI_URL` (default `http://127.0.0.1:3010`) |

Both need two accounts in one workspace, B a plain member invited by A:
`STAGING_EMAIL_A`, `STAGING_PASSWORD_A`, `STAGING_EMAIL_B`,
`STAGING_PASSWORD_B` (the browser suite signs in as A only). Passwords are
only ever sent in a request body; nothing prints them.

## What the API suite covers

- **Journeys** (`test_journeys.py`): sign in; B is a plain member; a real
  Decibyl reply (not failed, not stopped, not the "I could not think that
  through" apology); a new thread is private (B cannot list, read, draft
  from or write into it); an action card names exactly what it will do, is
  invisible to B, and two confirms sent at the same moment settle once
  (one 200, one 409, the item deleted once); the email send card carries
  the exact sender, recipient and words (proposed and declined, never
  sent); Today, a reminder through preview then save (a save that does not
  match the preview is refused), an event, the daily brief; saved items;
  tasks (and that the board is shared, by design); a meeting from notes
  (private to its creator); workspace memory and the memory manager;
  settings and models load for both people; Recents; usage.
- **Route sweep** (`test_sweep_routes.py`): every GET route with no path
  parameter in the API's own OpenAPI document, as A and as B, answers
  without a 5xx and within 5 s (`E2E_ROUTE_BUDGET_SECONDS`).
- **Privacy sweep** (`test_privacy_sweep.py`): A makes one of every private
  thing (a private thread, a temporary conversation, a private saved item,
  a reminder, an event, a meeting, a private commitment, personal
  instructions), each with its own marker. B reads every swept route and
  each item by id, and no marker may appear. A's own reads are the control:
  each marker must be visible to A somewhere, or the check proves nothing.
  Search routes are searched for the markers.

## What the browser suite covers

At 390 px and 1280 px: sign in through the form; Chat and Today are the
primary navigation; send a message and see the server's reply appear on
the page; open a thread from Recents; confirm an action card and watch it
settle once; Settings opens from the profile menu, and neither it, its
sections, Chat nor Today run off the screen edge. Every test also fails on
a console error, an uncaught page error or a 5xx. Failures keep a
screenshot and a trace.

## Skips are loud

A skip is never a pass, and every one says why:

- `flag <name> is off`: the route or journey is behind a switched-off flag.
  A flagged-off route answers a plain 404 by design; the sweeps name the
  flag from `api/route_flags.json`, which `python -m scripts.dump_route_flags`
  writes from the app and `api/tests/test_e2e_route_flags_current.py`
  keeps current.
- `not configured here: ...`: a 503 whose detail says a feature is not set
  up on this deployment (Google sign-in, TURN, Slack). An honest state, not
  a fault -- but visible in every run.
- `requires a model`: no provider key, so Decibyl answered with its apology.
  Staging sets `E2E_REQUIRE_MODEL=1`, which turns that into a failure.

## Rate limit

The API allows 600 requests a minute per client address, and both accounts
share one. The suites pace themselves (`E2E_REQUESTS_PER_MINUTE`, 400) and
wait out a 429 rather than read it as an answer: a 429 is below 500 and
contains no marker, so it would pass both sweeps for the wrong reason.

## Run it yourself

```bash
export STAGING_URL=http://127.0.0.1:8000 STAGING_UI_URL=http://127.0.0.1:3010
export STAGING_EMAIL_A=... STAGING_PASSWORD_A=... STAGING_EMAIL_B=... STAGING_PASSWORD_B=...

cd echowave/e2e/api && pip install -r requirements.txt && python -m pytest
cd echowave/e2e/browser && npm ci && npx playwright test
```

Against a local stack (`docs/contribution/setup.mdx`), switch the launch
flags on in `api/.env` (each `*_ENABLED` constant in
`api/services/features.py` `FLAGS`), run the ARQ worker (action cards and
replies are jobs), and verify both accounts' email once.

## What these suites cannot cover

- A call ringing, a WhatsApp arriving on a phone, Gmail consent in a
  browser: still the hand-checked list at the end of `staging_check.py`.
- Actually sending an email from a Decibyl address: the card is checked,
  the send is not, so a run never mails anyone.
- Personal memory facts: there is no endpoint that writes one; they are
  only learned during a model turn.
- Routes with a path parameter are swept only for the ids the journeys
  create, not exhaustively.
