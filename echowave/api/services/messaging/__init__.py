"""Sending a message on the carrier account the calls already run on.

A voice agent that finishes a useful call and cannot send the link is a voice
agent that made the customer write down a URL. In India the whole pattern is
call-then-text: the appointment is confirmed on the phone and the details arrive
as a message, because that is the artefact people keep.

**No new credentials.** The organisation already has a Twilio or Plivo account
configured for telephony, and both carriers send messages from the same account
with the same key. Asking for a second set to do a second thing with the same
vendor is a setup step that exists only because two features were built by two
people.

Deliberately small: one function, three providers, no queue of its own. Delivery
runs on the post-call task path alongside webhooks, because a follow-up message
is the same kind of work — something that happens after the conversation, from
the run's own context, and must not be able to delay hanging up.
"""

from api.services.messaging.send import (
    MessagingError,
    MessagingProviderNotSupported,
    SendResult,
    send_message,
    supported_providers,
)

__all__ = [
    "MessagingError",
    "MessagingProviderNotSupported",
    "SendResult",
    "send_message",
    "supported_providers",
]
