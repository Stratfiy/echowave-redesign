"""What the customer is told when their upload does not work.

The failure a customer sees is stored on the document row and rendered on the
files screen verbatim, so ``str(exc)`` of whatever escaped the task used to be
the copy. For an httpx failure that reads, in full:

    [Errno -2] Name or service not known

which tells the person who uploaded a policy PDF nothing they can act on, and
tells anyone looking over their shoulder our internal hostname.

So every failure that can reach a customer gets two texts: the exception
message, which goes to the log with whatever detail is useful, and
:func:`user_facing_message`, which is what the screen shows. The rule for the
second one is that it names what to do next, or says plainly that this one is
on us.
"""

from __future__ import annotations

import httpx

#: What a customer sees when the cause is ours, not theirs. Deliberately free
#: of hostnames, status codes and stack detail — none of it is actionable by
#: the person who uploaded the file, and some of it is ours to keep.
GENERIC_FAILURE = (
    "We could not process this document. The problem is on our side — "
    "support has the details. Try again in a few minutes, or contact support "
    "if it keeps happening."
)


class KnowledgeBaseError(Exception):
    """A failure that the customer can be told about honestly.

    ``user_message`` is the copy for the screen. It defaults to the exception
    text because these errors are written to be read by the customer in the
    first place — the subclasses below all pass something specific.
    """

    def __init__(self, message: str, *, user_message: str | None = None) -> None:
        super().__init__(message)
        self.user_message = user_message or message


class UnsupportedDocumentTypeError(KnowledgeBaseError):
    """The file is a kind we cannot read text out of."""


class DocumentExtractionError(KnowledgeBaseError):
    """The file is a supported kind but could not be read.

    Corrupt, truncated, or password-protected. Distinct from
    :class:`UnsupportedDocumentTypeError` because the remedy differs: one is
    "send a different format", the other is "send a working file".
    """


class EmptyDocumentError(KnowledgeBaseError):
    """The file was read successfully and contains no text to retrieve.

    The case that motivates a dedicated class is a scanned PDF: a page image
    with no text layer. Extraction succeeds, produces nothing, and — before
    this — the document was marked ``completed`` with zero chunks. The screen
    said ready and the agent knew nothing from it. That is the worst available
    outcome, so it is now an explicit failure with an explicit remedy.
    """


def user_facing_message(exc: BaseException) -> str:
    """The sentence to store on the document row and show the customer.

    Anything we raised deliberately carries its own copy. Anything else —
    a transport error, a provider 500, a bug — collapses to
    :data:`GENERIC_FAILURE`, because the alternative is leaking a stack trace
    or a hostname into the product.
    """
    if isinstance(exc, KnowledgeBaseError):
        return exc.user_message

    if isinstance(exc, httpx.HTTPError):
        # Covers connect errors, timeouts and non-2xx alike. A customer can do
        # nothing about any of them and the URL is not theirs to see.
        return GENERIC_FAILURE

    return GENERIC_FAILURE
