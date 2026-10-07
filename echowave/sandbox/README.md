# sandbox

The service that runs a bot's scripts in a box (working plan, Step 20).

One Docker container per job: `python:3.12-slim`, unprivileged, every
capability dropped, seccomp on, no new privileges, read-only root, a 64 MB
tmpfs to work in, 512 MB and one CPU by default, 256 processes, and
`--network none`. The api talks to this service over the compose network
behind `SANDBOX_SECRET`; this service holds no credentials and calls nothing.

The contract the api and a box keep is in
`api/services/sandbox/protocol.py`. The api bridges every tool call a script
makes: the box prints a request line, the api makes the call with the
account's connectors, and writes the result to the box's stdin.

Pull the box image once on the host so the first job does not wait on it:

    docker pull python:3.12-slim

Set `SANDBOX_URL=http://sandbox:8080` and `SANDBOX_SECRET` in `.env` for the
api; `SANDBOX_MAX_JOBS` (default 2) is how many boxes run at once.

## Site builds (Studio)

`POST /builds` installs and builds one Studio site: the files go in as a
tarball on the box's stdin, `npm install && npm run build` runs, and the
output folder comes back as base64 on stdout. A build box gets the same
hardening as a script box (unprivileged, no capabilities, read-only root,
tmpfs to work in) with more room (2 GB, two CPUs, a 1.5 GB `/work`) and one
network: `SANDBOX_BUILD_NETWORK`, which compose declares `internal` with the
`sandbox-registry` npm mirror as its only other member. A build can install
packages and reach nothing else — no api, no database, no metadata endpoint,
no internet. With `SANDBOX_BUILD_NETWORK` unset, builds are refused.

The service pulls the build image itself when it starts (see `/health`).

## Screenshots (Studio's design review)

`POST /screenshots` takes a built site and returns a laptop (1280px) and a
phone (390px) screenshot, the script errors the page threw, and how far it
scrolls sideways. The box has **no network**: Python's static server serves
the site on loopback and Chromium, driven by `shoot.mjs` over the DevTools
protocol, renders it with real mobile emulation. The service pulls this image
itself on start; until it has, a screenshot request answers 503 and says to
try again in a few minutes.

## Browsers (Decibyl's private browser)

`POST /browsers` starts one browser box -- browser-use on Chromium for one
person's task -- and then speaks the same three calls as a job
(`/jobs/{id}/next`, `/jobs/{id}/reply`, `DELETE /jobs/{id}`). Unlike every
other box it has the internet, on `SANDBOX_BROWSER_NETWORK` only: a bridge
this service creates with inter-container traffic off and nothing else on
it. The box's own proxy refuses private addresses for every request, and it
holds no credentials (its model calls come back to the api). With the
network unset, browsers are refused. See `browser/README.md` for the image,
the host egress guard and the install step.
