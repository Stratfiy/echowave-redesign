"""A sandbox key may not ring a stranger.

**What this proves, and what it does not.** These read the route's source
rather than calling it: importing it pulls in every telephony provider and the
pipecat transport layer, which is a running stack rather than a test. So this
shows the guard is present, that it is in the one path both endpoints funnel
through, and that it sits after the do-not-call check — the structural
mistakes, and the ones a later edit is most likely to make. It does not
exercise the guard against a live request; the decision it makes is unit
tested in `test_key_environment`.

Written this way deliberately rather than not written: the defect this feature
exists to prevent is somebody's customers being dialled by a key that was
handed to a contractor, and "the check was quietly moved above the DND gate"
is exactly the kind of change that would otherwise pass review.
"""

import pathlib

import pytest

from api.services.auth.key_environment import PRODUCTION, SANDBOX, is_sandbox

_MODULE = pathlib.Path(__file__).resolve().parents[1] / "routes" / "public_agent.py"


def _module_source() -> str:
    return _MODULE.read_text()


def _function_source(name: str) -> str:
    text = _module_source()
    start = text.index(f"async def {name}(")
    # Up to the next top-level definition, which is where this one ends.
    rest = text[start + 1 :]
    offsets = [
        rest.index(marker)
        for marker in ("\nasync def ", "\ndef ", "\n@router")
        if marker in rest
    ]
    end = min(offsets) if offsets else len(rest)
    return rest[:end]


@pytest.fixture
def source():
    return _function_source("_execute_resolved_target")


class TestTheGateExists:
    def test_the_shared_path_checks_the_environment(self, source):
        """Both the published and the draft endpoints funnel through here.

        Gating one route would have been easier and would have meant nothing:
        the draft endpoint dials a real phone just as hard.
        """
        assert "is_sandbox(key_environment)" in source

    def test_it_asks_whether_the_destination_is_verified(self, source):
        """The same gate the test-call button uses, which is the honest
        definition of "cannot ring your customers"."""
        assert "verified_numbers.is_verified" in source

    def test_an_unverified_destination_is_refused(self, source):
        assert "status_code=403" in source
        assert "refusal_message" in source

    def test_the_do_not_call_check_still_comes_first(self, source):
        """A number on the DND list must be refused as a DND violation
        whichever key aimed at it, not as a sandbox problem."""
        assert source.index("dnd.CallRefused") < source.index("is_sandbox(")


class TestBothEndpointsGoThroughIt:
    def test_neither_endpoint_executes_a_call_of_its_own(self):
        module = _module_source()
        # One shared execution path. A second one would be a second place to
        # remember this gate, and the one nobody remembers.
        assert module.count("async def _execute_resolved_target") == 1
        assert module.count("await _execute_resolved_target(") == 1

    def test_the_environment_comes_from_the_stored_column(self):
        """Never from the key string.

        The prefix exists so a human reading a config file can tell them
        apart; what a key may do is decided by the row.
        """
        source = _function_source("_initiate_call")
        assert 'getattr(api_key, "environment"' in source


class TestTheRunSaysWhichEnvironmentMadeIt:
    def test_every_run_is_stamped(self, source):
        """A sandbox call is not free — real speech recognition, a real model,
        a real carrier. What the environment buys is that the cost is
        attributable rather than absent."""
        assert 'initial_context["api_key_environment"]' in source

    def test_stamped_unconditionally(self, source):
        """Production runs too, or the split only has one side."""
        stamp = source.index('initial_context["api_key_environment"]')
        line_start = source.rindex("\n", 0, stamp)
        assert source[line_start:stamp].strip() == ""


class TestTheDefaultIsProduction:
    def test_the_shared_path_defaults_to_production(self, source):
        """A caller that forgets to pass one gets the ungated behaviour it had
        before this existed, rather than a call that mysteriously refuses."""
        assert f"key_environment: str = {PRODUCTION}" in source or (
            "key_environment: str = PRODUCTION" in source
        )

    def test_which_means_a_key_with_no_environment_is_gated_least(self):
        """Every key issued before this was issued to do real work."""
        assert is_sandbox(None) is False
        assert is_sandbox(SANDBOX) is True
