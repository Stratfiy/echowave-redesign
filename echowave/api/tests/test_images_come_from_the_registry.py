"""The box pulls images; it does not build them.

Deploys used to run `docker compose build` on the production host. Two things
followed, and the second cost more than the first:

* Build cache grew until the disk was 82% full — 419 entries, 116GB, on a 145GB
  volume. A full disk stops Postgres writing, which stops calls being costed and
  recordings being saved, and the error surfaces from whichever service wrote
  next rather than from the one at fault.
* A Next.js production build saturates all four vCPU, and those are the same
  cores that carry live calls, because ``run_pipeline_telephony`` runs inside
  the uvicorn workers. Deploying in business hours degraded every conversation
  in progress and presented as the model being slow.

``.github/workflows/build-images.yml`` builds both images and pushes them to
GHCR. These tests bind that workflow to ``docker-compose.yaml``: the image names
are written in two files, and before this they agreed only by coincidence.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yaml"
WORKFLOW = ROOT / ".github" / "workflows" / "build-images.yml"

#: The services whose images this project builds. Everything else in the compose
#: file is a third-party image (postgres, redis, nginx) and is not ours to push.
OURS = ("api", "ui")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _image(service: str) -> str:
    return _compose()["services"][service]["image"]


class TestTheWorkflowBuildsWhatComposePulls:
    @pytest.mark.parametrize("service", OURS)
    def test_the_workflow_has_a_matrix_entry_for_it(self, service):
        entries = _workflow()["jobs"]["build"]["strategy"]["matrix"]["include"]
        names = {entry["name"] for entry in entries}
        assert service in names, (
            f"compose pulls an image for '{service}' and the workflow builds "
            f"{sorted(names)} — the box would pull an image nothing publishes"
        )

    @pytest.mark.parametrize("service", OURS)
    def test_the_published_name_matches_the_pulled_name(self, service):
        """`decibyl-api` in one file and `decibyl_api` in the other is a pull
        that 404s at 3am, not a test failure at noon."""
        pulled = _image(service)
        assert f"/decibyl-{service}:" in pulled, pulled
        tags = " ".join(
            step.get("with", {}).get("tags", "")
            for step in _workflow()["jobs"]["build"]["steps"]
            if isinstance(step, dict)
        )
        assert f"decibyl-{service}:" in tags.replace("${{ matrix.name }}", service)

    @pytest.mark.parametrize("service", OURS)
    def test_no_service_of_ours_builds_on_the_box(self, service):
        """A `build:` key here sends the builder back to the production host,
        which is the whole thing this is meant to end. The local override lives
        in docker-compose.build.yaml and is not this file."""
        assert "build" not in _compose()["services"][service]


class TestRollbackIsAPull:
    @pytest.mark.parametrize("service", OURS)
    def test_the_tag_is_overridable(self, service):
        """Pinned to :latest, a rollback means rebuilding the previous commit on
        four cores that are carrying calls. With IMAGE_TAG it is
        `IMAGE_TAG=<sha> ./remote_up.sh`, which is a pull."""
        image = _image(service)
        assert "${IMAGE_TAG:-latest}" in image, (
            f"'{service}' is pinned as {image}; a rollback cannot select a "
            "previous image"
        )

    def test_every_image_is_tagged_with_the_commit_sha(self):
        """Without an immutable tag there is nothing to roll back TO: `latest`
        moves, and yesterday's `latest` is gone."""
        tags = " ".join(
            step.get("with", {}).get("tags", "")
            for step in _workflow()["jobs"]["build"]["steps"]
            if isinstance(step, dict)
        )
        assert "github.sha" in tags


class TestTheBuildCanActuallySucceed:
    def test_the_checkout_takes_submodules(self):
        """pipecat is a submodule and the API Dockerfile installs from it. A
        default checkout leaves the directory empty, and the failure reads like
        a broken dependency rather than a missing flag."""
        steps = _workflow()["jobs"]["build"]["steps"]
        checkout = next(
            s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
        )
        assert checkout.get("with", {}).get("submodules") == "recursive"

    def test_it_may_write_packages(self):
        """Without this permission the build succeeds and the push 403s, which
        looks like a registry problem and is not."""
        assert _workflow()["permissions"]["packages"] == "write"

    def test_the_build_context_is_the_repository_root(self):
        """The API Dockerfile needs pipecat/ beside api/, and the UI Dockerfile
        copies from ui/ by path. A service-directory context breaks both."""
        steps = _workflow()["jobs"]["build"]["steps"]
        build = next(
            s
            for s in steps
            if str(s.get("uses", "")).startswith("docker/build-push-action")
        )
        assert build["with"]["context"] == "."

    def test_the_cache_does_not_live_on_a_production_disk(self):
        """`type=gha` keeps it with the runner. A registry or local cache would
        reintroduce the storage problem somewhere else."""
        steps = _workflow()["jobs"]["build"]["steps"]
        build = next(
            s
            for s in steps
            if str(s.get("uses", "")).startswith("docker/build-push-action")
        )
        assert "type=gha" in build["with"]["cache-to"]


def test_the_registry_namespace_is_lowercased():
    """GHCR rejects an uppercase path and this repository's owner is
    'Stratfiy'. Interpolating it raw pushes nothing and the error names the
    tag rather than the case."""
    assert re.search(r"GITHUB_REPOSITORY_OWNER,,", WORKFLOW.read_text())
