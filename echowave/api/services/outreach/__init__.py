"""Outreach: find the people a business should write to, and write to them.

Two halves, kept apart on purpose:

* ``leads`` is the slot a lead-data provider plugs into. One interface,
  one key per provider in the existing provider-key vault (component
  ``data``), and an honest state when no key is there. Apollo is the
  provider implemented; another is a class and a registry line.
* ``tools`` is what Decibyl holds in Chat while the ``outreach`` flag is on:
  ``find_leads`` (a read) and ``draft_outreach`` (one send card per lead,
  on the person's own connected mailbox, nothing sent without Confirm).
"""
