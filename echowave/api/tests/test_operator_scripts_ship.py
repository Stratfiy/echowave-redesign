"""A script that documents a docker invocation must be in the image.

`scripts/` is not copied wholesale into the container — only an explicit list.
That is deliberate: the image should carry entrypoints and operator tools, not
every development helper in the repository.

The failure mode is that a script tells the operator exactly how to run it:

    docker compose exec api python -m scripts.seed_provider_rates --confirm

and the container answers "No module named scripts.seed_provider_rates". The
instruction is right there in the docstring, and it is wrong, and nothing says
why. seed_provider_rates shipped that way — which meant nobody could seed the
rate card, so every call on every deployment was recorded as uncosted.

So the rule this enforces is narrow and mechanical: if a script documents a
`docker compose exec api` invocation, it must be on the COPY list.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"
_DOCKERFILE = _REPO / "api" / "Dockerfile"

#: How a script advertises that it is meant to be run inside the container.
_DOCKER_INVOCATION = re.compile(
    r"docker\s+compose\s+exec\s+\S*api\S*\s+python\s+-m\s+scripts\.(\w+)"
)


def _scripts_documenting_a_docker_run() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SCRIPTS.glob("*.py")):
        for name in _DOCKER_INVOCATION.findall(path.read_text(encoding="utf-8")):
            found.add(name)
    return found


def _scripts_copied_into_the_image() -> set[str]:
    text = _DOCKERFILE.read_text(encoding="utf-8")
    return set(re.findall(r"\./scripts/(\w+)\.py", text))


def test_the_repository_layout_is_what_this_test_assumes():
    # A moved Dockerfile or scripts directory would make every assertion below
    # vacuously pass, which is worse than failing.
    assert _DOCKERFILE.is_file(), f"Dockerfile not found at {_DOCKERFILE}"
    assert _SCRIPTS.is_dir(), f"scripts/ not found at {_SCRIPTS}"
    assert any(_SCRIPTS.glob("*.py")), "no python scripts found to check"


@pytest.mark.parametrize(
    "script", sorted(_scripts_documenting_a_docker_run()) or ["<none>"]
)
def test_a_script_documenting_a_docker_run_is_in_the_image(script):
    if script == "<none>":
        pytest.skip("no script documents a docker invocation")

    assert script in _scripts_copied_into_the_image(), (
        f"scripts/{script}.py tells operators to run it with "
        f"`docker compose exec api python -m scripts.{script}`, but it is not "
        f"copied into the image. That command fails with 'No module named "
        f"scripts.{script}'. Add ./scripts/{script}.py to the COPY list in "
        f"api/Dockerfile."
    )


def test_seed_provider_rates_specifically_ships():
    """The script that found this hole.

    Without it, provider rates are never loaded, every call is uncosted rather
    than free, and reported margin is 100% and wrong.
    """
    assert "seed_provider_rates" in _scripts_copied_into_the_image()


def test_grant_superuser_still_ships():
    """The only route to the admin panel on a Docker install."""
    assert "grant_superuser" in _scripts_copied_into_the_image()
