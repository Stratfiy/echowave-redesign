"""Analytics events: the catalogue, the envelope and the outbox (handoff 36).

``emit`` is the one call an emitter needs; see ``outbox.py``.
"""

from api.services.events import catalogue, envelope
from api.services.events.outbox import emit

__all__ = ["catalogue", "emit", "envelope"]
