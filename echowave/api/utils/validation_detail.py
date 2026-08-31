"""What a raised ``ValueError`` should put in a 422's ``detail``.

Our validators reject a request by raising ``ValueError(payload)``, where the
payload is the thing the client should read -- a sentence, or a list of
``{"model": ..., "message": ...}`` that the settings screens render one line
per entry. Handlers reached for ``exc.args[0]`` to get it.

That index is a trap, because **pydantic's ``ValidationError`` is itself a
``ValueError``** and carries an empty ``args``. Any handler catching
``ValueError`` therefore caught it too, and then ``args[0]`` raised
``IndexError`` from inside the exception handler: what should have been a 422
naming the bad field became an unhandled 500 with no body at all. The client
falls back to its generic string, so the screen said "Failed to save model
configuration" and nothing else -- the one failure that most needed a reason
was the one that could not carry one.

Pydantic already knows what was wrong, so its errors are translated rather
than discarded. ``loc``/``msg`` is the shape FastAPI emits for its own 422s
and the shape the frontend's ``detailFromError`` already renders as
``field: message``, so a validation failure raised deep in a service now reads
on screen exactly like one caught at the request boundary.
"""

from __future__ import annotations

from pydantic import ValidationError


def detail_for(exc: ValueError) -> object:
    """The ``detail`` for a 422 raised from ``exc``.

    Never raises: a handler is a bad place to acquire a second bug, and an
    exception thrown while reporting an exception loses both.
    """
    if isinstance(exc, ValidationError):
        return exc.errors(
            # Dropped because none of the three survives contact with a
            # response: the url is a link to pydantic's docs, the context can
            # hold arbitrary objects, and the input is the value that was
            # rejected -- which, on these endpoints, is sometimes an API key.
            include_url=False,
            include_context=False,
            include_input=False,
        )
    if exc.args:
        return exc.args[0]
    # An argless ValueError says only that something was refused. Better than
    # the 500 this used to become, and better than a lie about which field.
    return str(exc) or "The request could not be validated."
