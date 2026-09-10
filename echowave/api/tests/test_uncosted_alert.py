"""Staff hear about a settlement with no rate, once a day per model."""

from api.services.billing import uncosted_alert


def test_the_mail_names_the_models_and_the_consequence():
    subject, body = uncosted_alert.compose(
        organization_id=42, workflow_run_id=7, labels=["tts:sarvam/bulbul-v3"]
    )
    assert "tts:sarvam/bulbul-v3" in subject
    assert "organization 42" in body
    assert "charged nothing" in body
    assert "/superadmin/billing/uncosted" in body


import pytest


@pytest.mark.asyncio
async def test_nothing_to_say_sends_nothing():
    assert (
        await uncosted_alert.notify(organization_id=1, workflow_run_id=1, labels=[])
        is False
    )
