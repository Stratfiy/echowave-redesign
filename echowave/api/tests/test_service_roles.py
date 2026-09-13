"""What each role starts, and the default that must never drift.

``DECIBYL_ROLE`` exists because running the same container on a second machine
ran a second campaign orchestrator and a second ARI manager. Neither errors:
the orchestrator dedupes with an in-memory dict and subscribes to a Redis
pub/sub channel, which fans out to every subscriber, so N nodes means N times
the dial attempts and the first evidence is the carrier bill.

That failure has no symptom in a log, so these tests assert the composition
directly. ``DECIBYL_PRINT_PLAN=1`` makes the start script print what it would
launch and exit without launching anything or touching the database, which is
what makes the wiring checkable before a deploy rather than during one.

The most important test here is the boring one: ``role=all`` must start exactly
what the script started before roles existed. Every deployed box today sets no
role at all, so a change that quietly alters the default would take the
platform down on the next restart with nothing pointing at the cause.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "start_services_docker.sh"

#: What a single box has always run. Named here rather than derived, so the
#: test disagrees with a change to the default instead of following it.
ALL_ROLE_SERVICES = {
    "migrations",
    "ari_manager",
    "campaign_orchestrator",
    "uvicorn0",
    "arq1",
}

#: Exactly one of each of these may exist across the whole deployment.
SINGLETONS = {"ari_manager", "campaign_orchestrator"}


def plan(role: str | None = None, **env: str) -> list[str]:
    """The services this role would start, in order."""
    environment = {
        **os.environ,
        "DECIBYL_PRINT_PLAN": "1",
        "FASTAPI_WORKERS": "1",
        "ARQ_WORKERS": "1",
        **env,
    }
    if role is not None:
        environment["DECIBYL_ROLE"] = role
    else:
        environment.pop("DECIBYL_ROLE", None)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [
        line.removeprefix("PLAN ").strip()
        for line in result.stdout.splitlines()
        if line.startswith("PLAN ")
    ]


class TestTheDefaultHasNotMoved:
    """Every deployed box sets no role. This is the test that protects them."""

    def test_no_role_starts_everything_a_single_box_always_started(self):
        assert set(plan(None)) == ALL_ROLE_SERVICES

    def test_all_is_the_same_as_setting_nothing(self):
        """If these ever disagree, one of the two paths has been edited alone."""
        assert plan(None) == plan("all")


class TestAMediaNodeIsSafeToDuplicate:
    """The whole point of the role: adding a machine must not add a singleton."""

    def test_a_media_node_starts_no_singleton(self):
        assert SINGLETONS.isdisjoint(plan("media"))

    def test_a_media_node_does_not_migrate(self):
        """Three nodes booting together would otherwise run three concurrent
        migrations against one database."""
        assert "migrations" not in plan("media")

    def test_a_media_node_runs_no_post_call_work(self):
        """Kept off deliberately, so a costing job cannot take CPU from a live
        call. arq itself is safe to duplicate -- it pulls from a queue."""
        assert not [name for name in plan("media") if name.startswith("arq")]

    def test_a_media_node_serves_requests(self):
        """A role that starts nothing is a container that sits there looking
        healthy while answering no calls."""
        assert plan("media") == ["uvicorn0"]

    def test_every_worker_gets_its_own_port_process(self):
        """uvicorn --workers would pin a long-lived WebSocket to whichever
        worker accepted it, so these are separate processes on consecutive
        ports with nginx balancing least_conn across them."""
        assert plan("media", FASTAPI_WORKERS="4") == [
            "uvicorn0",
            "uvicorn1",
            "uvicorn2",
            "uvicorn3",
        ]


class TestAControlNodeOwnsWhatOnlyOneMachineMayDo:
    def test_a_control_node_runs_the_singletons_and_the_migrations(self):
        started = set(plan("control"))
        assert SINGLETONS <= started
        assert "migrations" in started

    def test_a_control_node_serves_no_http(self):
        """It runs no uvicorn, so nothing should route to it -- the load
        balancer goes in front of the media nodes only."""
        assert not [name for name in plan("control") if name.startswith("uvicorn")]

    def test_between_them_media_and_control_cover_everything(self):
        """A two-machine split must not silently drop a service. This is the
        test that notices when a fifth service is added to one branch only."""
        assert set(plan("media")) | set(plan("control")) == ALL_ROLE_SERVICES


class TestAWrongRoleIsRefused:
    def test_a_typo_does_not_fall_back_to_all(self):
        """Defaulting would start a second orchestrator on a node meant to be
        media-only -- the exact failure the role exists to prevent, and one
        that produces no error of its own."""
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env={**os.environ, "DECIBYL_PRINT_PLAN": "1", "DECIBYL_ROLE": "medai"},
            timeout=60,
        )
        assert result.returncode != 0
        assert "medai" in result.stderr

    @pytest.mark.parametrize("role", ["all", "media", "control"])
    def test_no_role_is_empty(self, role):
        assert plan(role), f"role={role} starts nothing"
