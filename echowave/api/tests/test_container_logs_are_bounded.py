"""Every long-running container must cap its own log.

Docker's default ``json-file`` driver has no size limit. A container logs until
the disk is full, and then Postgres cannot write, calls cannot be costed and
recordings cannot be saved -- so one unbounded log takes the whole platform
down. The symptom is "no space left on device" from whichever service happened
to write next, never from the one actually at fault, which is why this is worth
a test rather than a convention.

It was found the way these things are: a production box at 82% with 118GB used
and nothing obviously large in the application's own data. For a long time only
``postgres`` carried the cap, which meant the database was the one service that
could not cause the outage and every other one could.

An allowlist would be the wrong shape here -- a new service added without a cap
is exactly the case that must fail -- so this reads the compose file and
requires the block of everything that stays running, with a named exemption for
the one container that exits on its own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yaml"

#: Containers that run to completion and stop, so their log cannot grow without
#: bound. Named individually with the reason, because "it looked short-lived"
#: is how an unbounded service gets waved through.
SHORT_LIVED = {
    # Renders the nginx and coturn configs at startup and exits.
    "decibyl-init",
}

#: Generous for debugging, bounded for a box: 3 files at 10MB is 30MB a service.
MAX_TOTAL_MEGABYTES = 100


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _megabytes(value: str) -> float:
    text = str(value).strip().lower()
    for suffix, factor in (("k", 1 / 1024), ("m", 1), ("g", 1024)):
        if text.endswith(suffix):
            return float(text[:-1]) * factor
    return float(text) / (1024 * 1024)


LONG_RUNNING = sorted(set(_services()) - SHORT_LIVED)


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_every_long_running_service_caps_its_log(name):
    service = _services()[name]
    logging = service.get("logging")
    assert logging, (
        f"'{name}' has no logging block, so it uses Docker's default json-file "
        "driver with NO size limit. It will fill the disk and the failure will "
        "land on whichever service writes next."
    )
    options = logging.get("options") or {}
    assert options.get("max-size"), f"'{name}' sets no max-size"
    assert options.get("max-file"), (
        f"'{name}' sets max-size but no max-file, so Docker keeps every rotated "
        "file and the cap buys nothing"
    )


@pytest.mark.parametrize("name", LONG_RUNNING)
def test_the_cap_is_small_enough_to_matter(name):
    """A cap that allows gigabytes is a cap in name only."""
    options = _services()[name]["logging"]["options"]
    total = _megabytes(options["max-size"]) * int(options["max-file"])
    assert total <= MAX_TOTAL_MEGABYTES, (
        f"'{name}' allows {total:.0f}MB of logs; the budget is "
        f"{MAX_TOTAL_MEGABYTES}MB a service"
    )


def test_the_exemption_list_names_only_services_that_exist():
    """A renamed service would otherwise stay silently exempt forever."""
    missing = SHORT_LIVED - set(_services())
    assert not missing, f"exempted services that no longer exist: {sorted(missing)}"


def test_there_is_something_to_check():
    """If the compose file moved, every parametrised test above would vanish
    and this suite would pass by being empty."""
    assert len(LONG_RUNNING) >= 8
