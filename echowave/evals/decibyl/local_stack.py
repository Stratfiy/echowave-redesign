"""The real API or worker, locally, with Decibyl's model replaced by the fake.

For developing the evals without a provider key: every route, the worker,
the tools, the cards and the thread are the product's own; only the model's
words come from ``fake.respond``. Never used on staging.

    cd echowave
    set -a; source <your local env>; set +a          # DATABASE_URL, REDIS_URL, flags
    FAKE_MODEL_LOG=/tmp/fake-model.jsonl python -m evals.decibyl.local_stack api --port 8000 &
    FAKE_MODEL_LOG=/tmp/fake-model.jsonl python -m evals.decibyl.local_stack worker &
    STAGING_URL=http://127.0.0.1:8000 ... python -m evals.decibyl.run --no-judge

``FAKE_MODEL_LOG`` keeps, for each model call, the tail of the system prompt
and the question, so what the real prompt told the model can be read back.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("process", choices=("api", "worker"))
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    from evals.decibyl import fake

    fake.install()
    if args.process == "api":
        import uvicorn

        uvicorn.run(
            "api.app:app", host="127.0.0.1", port=args.port, log_level="warning"
        )
        return 0
    from arq import run_worker

    from api.tasks.arq import WorkerSettings

    run_worker(WorkerSettings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
