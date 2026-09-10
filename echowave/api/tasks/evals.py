"""Run one eval result in the worker; the route only enqueues."""

from __future__ import annotations

from api.services.evals.runner import run_case


async def run_eval_case(ctx, result_id: int) -> None:
    await run_case(int(result_id))
