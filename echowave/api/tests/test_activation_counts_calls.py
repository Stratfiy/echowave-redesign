"""The activation funnel's "first call" step has to mean a call.

A text chat is also a workflow run, so counting rows without looking at the
mode said an account that typed into the chat widget and never dialled anybody
had reached the step the whole funnel exists to measure — the moment the
product is proven to them. That is the number a decision about the product
gets made on.
"""

import inspect

import pytest

from api.enums import NON_VOICE_RUN_MODES, WorkflowRunMode, is_voice_run_mode


class TestWhichModesAreCalls:
    @pytest.mark.parametrize(
        "mode",
        [
            WorkflowRunMode.TWILIO,
            WorkflowRunMode.PLIVO,
            WorkflowRunMode.TELNYX,
            WorkflowRunMode.VONAGE,
            WorkflowRunMode.VOBIZ,
            WorkflowRunMode.CLOUDONIX,
            WorkflowRunMode.ARI,
            WorkflowRunMode.WEBRTC,
            WorkflowRunMode.SMALLWEBRTC,
            WorkflowRunMode.STASIS,
            WorkflowRunMode.VOICE,
        ],
    )
    def test_a_call_is_a_call(self, mode):
        assert is_voice_run_mode(mode.value) is True

    @pytest.mark.parametrize("mode", [WorkflowRunMode.TEXTCHAT, WorkflowRunMode.CHAT])
    def test_typing_is_not(self, mode):
        assert is_voice_run_mode(mode.value) is False

    @pytest.mark.parametrize("mode", [None, ""])
    def test_a_run_with_no_mode_is_not_counted(self, mode):
        """`mode` is non-nullable, so this is a row that should not exist.

        Counting it would be inventing an activation from a broken record.
        """
        assert is_voice_run_mode(mode) is False


def test_every_mode_is_classified():
    """The guard on the exception list.

    Voice modes are the default so that adding a telephony provider does not
    silently drop its calls out of the funnel. The price of that is that a new
    *text* mode would quietly count as a call — unless adding one fails here.
    """
    classified = {
        WorkflowRunMode.TWILIO,
        WorkflowRunMode.PLIVO,
        WorkflowRunMode.TELNYX,
        WorkflowRunMode.VONAGE,
        WorkflowRunMode.VOBIZ,
        WorkflowRunMode.CLOUDONIX,
        WorkflowRunMode.ARI,
        WorkflowRunMode.WEBRTC,
        WorkflowRunMode.SMALLWEBRTC,
        WorkflowRunMode.STASIS,
        WorkflowRunMode.VOICE,
        WorkflowRunMode.TEXTCHAT,
        WorkflowRunMode.CHAT,
    }
    unclassified = set(WorkflowRunMode) - classified
    assert not unclassified, (
        f"{[m.name for m in unclassified]} is a new run mode. Decide whether it "
        "is a call, add it to NON_VOICE_RUN_MODES if it is not, and list it here."
    )


def test_the_funnel_actually_filters():
    """Checked in the source, because what went wrong was an absent WHERE.

    Pricing this properly needs a database; the defect was one missing clause
    and it is visible in the text.
    """
    from api.db import activation_client

    source = inspect.getsource(activation_client.ActivationClient.activation_funnel)
    assert "NON_VOICE_RUN_MODES" in source


def test_the_exception_list_is_not_empty():
    """An empty one would pass every test above while filtering nothing."""
    assert NON_VOICE_RUN_MODES
