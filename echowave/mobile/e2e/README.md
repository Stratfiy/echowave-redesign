# The phone-size walkthrough

How the screenshots in the PR were made: the app's web build, walked with
Playwright at 390x844 (iPhone 12-15 size, 2x), against a local stack. Local
only -- never point this at staging or production.

1. A local stack with every launch flag on (`api/services/features.py`
   `FLAGS` all `true`, plus `ENVIRONMENT=local`, `AUTH_PROVIDER=local`),
   Postgres with pgvector, Redis, an S3-compatible store for uploads (moto
   or MinIO), `uvicorn api.app:app` on :8000 and the ARQ worker. The web
   app (`ui`, `next dev` on :3000) only for the web-view shot.
2. An account: an invite row (`signup_invites`), then sign up through
   `POST /api/v1/auth/signup` or the app.
3. The demo thread: `PYTHONPATH=. python mobile/e2e/seed_walkthrough.py 1 1 <thread-id>`
   from `echowave/` (see its docstring: replies need a model key the
   walkthrough box does not have, so a reply, a real card and a connect chip
   are written with the server's own services).
4. Build and serve the app:
   ```bash
   EXPO_PUBLIC_API_URL=http://localhost:8000 EXPO_PUBLIC_WEB_URL=http://localhost:3000 \
   EXPO_PUBLIC_DEMO_CONTACTS=1 npx expo export --platform web --output-dir /tmp/web-dist
   node e2e/serve.mjs /tmp/web-dist        # :8081, SPA fallback
   ```
   `EXPO_PUBLIC_DEMO_CONTACTS=1` gives the web build four sample contacts
   (labelled "sample" on screen) because a browser has no address book.
5. From a directory with `playwright` installed (`CHROMIUM_PATH` if the
   browser is not Playwright's own): `node sign-in.mjs` (signs in, saves
   `state.json`), then `node walk.mjs thread approval confirm newchat today
   people voice share settings simple hindi dark webview`; screenshots land
   in `out/`. `SCHEME=dark` for the dark pass.

What a browser cannot show, and was tested otherwise: biometric unlock,
push delivery, the OS share sheet (the same screen opens from
`decibyl://share?text=...`), camera and photo pickers, and a live voice
session (the local stack has no speech keys, so the screen shows the
server's honest "needs setup").
