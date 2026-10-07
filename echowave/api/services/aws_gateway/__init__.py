"""Claude and other models through AWS.

Two things live here, both off by default and both read from configuration
(``api/constants.py``, "Claude and other models through AWS"):

* **Where Claude runs** for the managed tiers -- ``CLAUDE_BACKEND`` is
  ``anthropic`` (today), ``aws_platform`` (Claude Platform on AWS) or
  ``bedrock`` (Amazon Bedrock). See :mod:`.claude`.
* **A model gateway beyond Claude** on Bedrock: a fallback brain when Claude
  fails (:mod:`.fallback`), a cheap tier for sorting work (:mod:`.cheap`),
  embeddings (:mod:`.embeddings`) and Nova Sonic speech-to-speech for Hindi
  and Indian English (:mod:`.nova_sonic`).

Every choice reports one of the honest capability states (``config.Status``):
available, needs setup, or disabled. A Bedrock model is "needs setup" until
the operator lists it in ``BEDROCK_ENABLED_MODELS`` after granting model
access, and goes back to "needs setup" the moment AWS refuses it at runtime.
Nothing here ever falls through silently.
"""
