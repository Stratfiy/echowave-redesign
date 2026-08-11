"""Operational notices to customers.

Notices, not marketing. Everything here is something a customer needs in order
to keep their service working — a balance about to run out, a number about to
be suspended — and every one of them is sent at most once per account per day
per kind, recorded in ``notifications`` so a retried job does not send it twice.
"""
