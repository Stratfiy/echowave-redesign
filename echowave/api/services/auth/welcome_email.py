"""The first thing Decibyl says to somebody who just signed up.

Signup sent a six-digit verification code and then nothing. A code is not a
welcome: it is a challenge, it says nothing about what the account can do, and
it is the only message in the inbox from a company the person has just handed
their email to. The next thing they heard from us was either a receipt or a
low-balance warning.

**Not a marketing email.** The positioning in one line (founder decision,
9 Oct 2026, AGENTS.md), then three things: what to do first, that it learns
with the person's approval, and where their own keys live. No plan, price or
balance -- no pricing is shown to users. Anything else belongs on the site.

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

Decibyl is an intelligent agent that grows and evolves with you: one
personal assistant for life and work. Talk in your language, give it a task,
and let it help you follow through.

Three things worth knowing:

1. Start with a task — {base}/overview
   Ask for anything. If it needs an app, Decibyl asks then, not before.

2. It learns how you like things done
   It learns your preferences and, with your approval, gets better at your
   work over time.

3. Your own keys, if you want them — {base}/provider-keys
   If you would rather run models on your own provider keys, they live there.

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
