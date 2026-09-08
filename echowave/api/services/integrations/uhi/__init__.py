"""ABDM's Unified Health Interface, and the one hard part of talking to it.

See `docs/audits/2026-09-08-abdm-uhi-assessment.md` for whether to build on
UHI at all. This package exists for the part of the answer that does not
depend on the protocol settling: **discovery is a broadcast, and a caller is
on the line while it happens.**
"""

from api.services.integrations.uhi.discovery import (
    DiscoveryCollector,
    DiscoveryResult,
)

__all__ = ["DiscoveryCollector", "DiscoveryResult"]
