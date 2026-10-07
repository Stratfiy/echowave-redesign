"""Amazon Nova Sonic speech-to-speech, for Hindi and Indian English only.

Offered as the ``nova`` speech-to-speech tier while ``aws_nova_sonic`` is on
and ``NOVA_SONIC_MODEL``, ``NOVA_SONIC_REGION`` and ``NOVA_SONIC_VOICE`` are
set. Sarvam stays the default for every Indian language, Hindi included:
this is a latency choice for the two languages Nova Sonic speaks, never a
replacement for the cascade. Another language on this tier is moved to the
natural tier at compile time (``managed_tiers.realtime_tier_for_language``).

Nova Sonic runs in its own region (``NOVA_SONIC_REGION``), which may not be
the one the rest of the platform runs in; DEPLOY.md says what that means for
where audio is processed.

pipecat's service takes explicit credentials rather than a session, so the
current ones are read from the AWS credential chain (the instance role) at
call start. Temporary role credentials outlast a phone call comfortably.
"""

from __future__ import annotations

from typing import Any

from api import constants

#: The key-slot marker for a managed Nova Sonic section; see
#: ``aws_gateway.claude`` for why a marker rather than a key.
CREDENTIAL = "aws-iam:nova_sonic"


def _credentials() -> Any:
    import botocore.session

    found = botocore.session.get_session().get_credentials()
    if found is None:
        raise RuntimeError(
            "No AWS credentials for Nova Sonic: attach the instance role "
            "described in DEPLOY.md."
        )
    return found.get_frozen_credentials()


def build_service(*, model: str) -> Any:
    """The pipeline's speech-to-speech service on Nova Sonic."""
    from pipecat.services.aws.nova_sonic.llm import (
        AWSNovaSonicLLMService,
        AWSNovaSonicLLMSettings,
    )

    creds = _credentials()
    return AWSNovaSonicLLMService(
        secret_access_key=creds.secret_key,
        access_key_id=creds.access_key,
        session_token=creds.token,
        region=constants.NOVA_SONIC_REGION,
        settings=AWSNovaSonicLLMSettings(
            model=model or constants.NOVA_SONIC_MODEL,
            voice=constants.NOVA_SONIC_VOICE,
        ),
    )
