"""Whether a run keeps its audio.

The pipeline records every call unless the agent says otherwise, and the
switch is read in exactly one place so "recording off" means the same thing
everywhere it matters: no audio buffered in memory, nothing uploaded, and no
disclosure spoken to the caller about a recording that does not exist. The
transcript, the usage and the outcome are still written — those are what the
call review and the bill are built from.
"""

from __future__ import annotations

from typing import Any, Mapping


def recording_enabled(run_configs: Mapping[str, Any] | None) -> bool:
    """Read the per-agent recording switch off stored workflow configurations.

    Only an explicit ``enabled: false`` turns it off. A missing block, a
    missing key, or a malformed value all mean the platform default, which is
    on — an agent built before the switch existed keeps its recordings.
    """
    if not run_configs:
        return True
    block = run_configs.get("recording_configuration")
    if isinstance(block, Mapping):
        return block.get("enabled") is not False
    enabled = getattr(block, "enabled", None)
    return enabled is not False
