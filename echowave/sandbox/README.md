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
