"""Decibyl in your apps (DCH-1, KAN-277).

The owner talks to Decibyl from WhatsApp, Telegram, Slack or Teams, and
confirms its cards there. Each platform is an adapter (``base.Adapter``):
it verifies and parses the platform's webhook into ``Inbound`` and sends
text and cards back. ``dispatch`` does the rest, the same for every one:
who is writing (``identities``), link codes, a line to Decibyl, a tap to
``actions.settle``.
"""
