"""A full disk must not be a state the box cannot deploy its way out of.

On 16 Sep 2026 the production box reached 100% of a 145G disk and every
deploy failed: the SSM agent could not write its own output, so a merge
carrying five customer-facing fixes sat on `main` unshipped.

The cause was not a missing prune. There was one, and it did nothing.

* It kept a **week** of build cache. The box builds its images locally --
  it cannot pull them -- so each deploy adds one to two gigabytes, and at
  ten merges a day a seven-day window is thirty deploys of cache. The
  entries filling the disk were 42 minutes, 4 hours and 3 days old. None
  were old enough for an age filter to touch.
* It ran **only after a successful deploy**. Once the disk was full every
  deploy failed, and a failed deploy never reached the prune -- so the one
  thing that would have freed space stopped running. The box could not
  recover without a human opening a shell on it.

Both halves are guarded here.
"""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_deploy.sh"


def _source() -> str:
    return SCRIPT.read_text()


def _body(name: str) -> str:
    return _source().split(f"{name}() {{", 1)[1].split("\n}", 1)[0]


class TestTheCacheIsBoundedBySizeNotAge:
    def test_the_prune_asks_for_a_size_ceiling(self):
        # An age filter cannot bound a cache whose growth rate is the deploy
        # rate. A size cap holds whatever that rate is.
        assert "--keep-storage" in _body("prune_disk")

    def test_the_age_filter_survives_as_a_fallback(self):
        # Older daemons do not know --keep-storage. Losing the age filter
        # would leave such a box with no pruning at all.
        assert "PRUNE_BUILD_CACHE_UNTIL" in _body("prune_disk")

    def test_the_ceiling_is_overridable_without_editing_the_script(self):
        assert re.search(
            r'PRUNE_BUILD_CACHE_KEEP="\$\{PRUNE_BUILD_CACHE_KEEP:-[^}]+\}"', _source()
        )


class TestADeployClearsRoomBeforeItBuilds:
    def test_ensure_room_runs_before_the_build(self):
        source = _source()
        assert source.index("ensure_room\n") < source.index("docker compose build")

    def test_it_only_prunes_the_build_cache_and_dangling_images(self):
        # Never an image the rollback might need: this runs *before* the
        # health check, unlike the housekeeping at the end.
        body = _body("ensure_room")
        assert "docker builder prune" in body
        assert "docker image prune -f" in body
        assert "docker image prune -a" not in body
        assert "--volumes" not in body

    def test_it_never_fails_the_deploy(self):
        # A box that cannot free space should still get to try, and fail on
        # the build with an error that says so.
        for line in _body("ensure_room").splitlines():
            if "docker " in line:
                assert "|| true" in line, line

    def test_the_floor_is_overridable(self):
        assert re.search(r'MIN_FREE_GB="\$\{MIN_FREE_GB:-\d+\}"', _source())


class TestTheFunctionsActuallyRun:
    """Source-reading tests pass on a script that does not execute. These
    run the two new functions in a real shell."""

    def _run(self, snippet: str) -> str:
        source = _source()
        # Take the definitions without running the deploy around them.
        defs = "\n".join(
            [
                "free_gb() {" + _body("free_gb") + "\n}",
                'say() { echo "$*"; }',
                "docker() { return 0; }",
                "MIN_FREE_GB=999999",
                "ensure_room() {" + _body("ensure_room") + "\n}",
            ]
        )
        assert "free_gb" in source
        return subprocess.run(
            ["bash", "-c", defs + "\n" + snippet],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def test_free_gb_returns_a_number(self):
        out = self._run("free_gb").strip()
        assert out.isdigit(), out

    def test_ensure_room_clears_when_below_the_floor(self):
        # The floor is set absurdly high, so it must decide to clear.
        out = self._run("ensure_room")
        assert "clearing build cache" in out

    def test_ensure_room_is_quiet_when_there_is_room(self):
        out = self._run("MIN_FREE_GB=0; ensure_room")
        assert "clearing build cache" not in out
