"""Image generation: posters and ad creatives (``image_generation``).

* ``providers/`` -- Gemini, OpenAI and Amazon Bedrock (Nova Canvas) behind
  one small interface; ``registry`` lists them.
* ``keys`` -- which provider a workspace chose and the key it runs on (the
  BYOK vault, or the platform's key when one is configured).
* ``guard`` -- never invent a fact: the brief's text is checked against what
  the person said, and the provider prompt is composed from the brief.
* ``service`` -- one request: check, resolve, generate, store, meter, card.
* ``tools`` -- the ``make_images`` tool, for Decibyl and for agents.
* ``offer`` -- the provider card on the thread.
* ``store`` -- bytes in the workspace's bucket, rows in ``generated_images``.
* ``metering`` -- the vendor's cost per image, at a customer rate of zero.
"""

from __future__ import annotations

from api.services import features

FEATURE = "image_generation"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FEATURE, organization_id)
