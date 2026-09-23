"""A request the service layer turns down, in words the person can act on.

Raise a subclass of :class:`Refused` from a service and the app answers 400
with its message (see ``api/app.py``) -- so a route stays a thin call with no
``try``/``except`` around it just to translate the error.
"""


class Refused(ValueError):
    """Turned down for a reason the person can fix; its message says how."""


__all__ = ["Refused"]
