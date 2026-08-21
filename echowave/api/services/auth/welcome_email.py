"""The first thing Decibyl says to somebody who just signed up.

Signup sent a six-digit verification code and then nothing. A code is not a
welcome: it is a challenge, it says nothing about what the account can do, and
it is the only message in the inbox from a company the person has just handed
their email to. The next thing they heard from us was either a receipt or a
low-balance warning.

**Not a marketing email.** Three things, because those are the three that
decide whether the account is ever used: what to do first, what it will cost
when they do, and where the keys and numbers live. Anything else belongs on the
site.

**Not a second verification email either.** It says nothing about the code —
that mail is already in flight and repeating it here would make two messages
compete for the same click.
"""

from __future__ import annotations

from api.services.messaging.announce import Notice

KIND = "welcome"


def compose(*, account_name: str | None, app_url: str) -> Notice:
    """What to send a brand-new account.

    ``account_name`` is whatever we know them by and is routinely nothing at
    all — Google gives a name, the password form may not — so the greeting is
    written to read correctly either way rather than falling back to "Hi ,".
    """
    base = (app_url or "").rstrip("/")
    greeting = f"Welcome, {account_name}." if account_name else "Welcome to Decibyl."

    body = f"""{greeting}

Your account is ready. Three things worth knowing before your first call:

1. Build an agent — {base}/workflow/create
   Pick how it should sound and think. Every option shows what it costs a
   minute before you commit to it.

2. Connect a number — {base}/telephony-configurations
   Either bring your own carrier account, or take a number from us. On your own
   account the carrier bills you for the minutes directly; on ours they appear
   on your Decibyl invoice.

3. Add balance when you are ready — {base}/billing
   Calls run down a prepaid balance. Nothing is charged until you place one,
   and the balance page always shows what a minute costs on your setup.

If you would rather run models on your own provider keys, they live at
{base}/provider-keys — we only ask for the ones covering models we do not
already offer.

Reply to this email if anything is unclear. A person reads it.

— Decibyl
"""

    return Notice(
        subject="Your Decibyl account is ready",
        body=body,
        # One per account, for ever. Re-provisioning a half-finished signup —
        # which both front doors can do — must not welcome somebody twice.
        dedupe_key="welcome",
    )
