#!/usr/bin/env python3
"""Run the six checks against a Decibyl call and print what they say.

Reads the API key from ``DECIBYL_KEY`` and never accepts it as an argument, so
it cannot end up in a shell history, a log line or a commit.

    DECIBYL_KEY=... python3 diagnose_run.py 17 302   # one run
    DECIBYL_KEY=... python3 diagnose_run.py 17       # the last few runs

Every number it prints comes from the run record. Where it offers an opinion
it says so, and the opinions are deliberately weak: one call is one data point
and this script cannot tell a stuck agent from a caller who hung up without
the turn count it prints beside it.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://app.decibyl.ai/api/v1"

#: Unicode blocks, and what a run of one means. A caller appearing in three of
#: these in one call is a transcriber guessing, not a polyglot.
SCRIPTS = (
    (0x0900, 0x097F, "devanagari"),
    (0x0980, 0x09FF, "bengali"),
    (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"),
    (0x0B00, 0x0B7F, "odia"),
    (0x0B80, 0x0BFF, "tamil"),
    (0x0C00, 0x0C7F, "telugu"),
    (0x0C80, 0x0CFF, "kannada"),
    (0x0D00, 0x0D7F, "malayalam"),
    (0x0400, 0x04FF, "cyrillic"),  # never legitimate here; a sign of garbage in
)

#: Caller turns before a call that never moved counts as stuck rather than
#: short. Measured across twenty-one runs: working agents had left their first
#: node by the caller's third turn.
STUCK_AFTER_TURNS = 4


def api(path: str, **query):
    key = os.environ.get("DECIBYL_KEY")
    if not key:
        sys.exit("Set DECIBYL_KEY in the environment. Do not pass it as an argument.")
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    request = urllib.request.Request(url, headers={"X-API-Key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 + 2 * attempt)


def signed(key: str) -> str | None:
    """Storage keys are not URLs. Transcripts and recordings need signing."""
    if not key:
        return None
    return api("/s3/signed-url", key=key).get("url")


def scripts_in(text: str) -> set[str]:
    return {name for low, high, name in SCRIPTS if any(low <= ord(c) <= high for c in text)}


def diagnose(workflow_id: int, run_id: int) -> None:
    run = api(f"/workflow/{workflow_id}/runs/{run_id}")
    context = run.get("gathered_context") or {}
    events = (run.get("logs") or {}).get("realtime_feedback_events") or []
    usage = (run.get("usage_info") or {}).get("llm") or {}

    print(f"\n{'=' * 62}\nrun {run_id} on workflow {workflow_id}\n{'=' * 62}")

    # 2. Which model actually served this call. A tier is not a model.
    brains = [k.split("|||")[-1] for k in usage if "Service" in k] or ["unknown"]
    print(f"brain served        : {', '.join(brains)}")

    # 1. Did it move between steps at all?
    visited = context.get("nodes_visited") or []
    turns = len([e for e in events if e.get("type") == "rtf-user-transcription"])
    transitions = len([e for e in events if e.get("type") == "rtf-node-transition"])
    print(f"nodes visited       : {len(visited)} {visited}")
    print(f"caller turns        : {turns}   (node transitions: {transitions})")
    if len(visited) <= 1 and turns >= STUCK_AFTER_TURNS:
        print("  >> STUCK. It talked for a while and never left its first step.")
        print("     Every step change is a tool call; suspect the model or the edges.")
    elif len(visited) <= 1:
        print("  -- one node, but too few caller turns to call it stuck.")

    # 5. Did the flow collect anything?
    extracted = context.get("extracted_variables") or {}
    filled = {k: v for k, v in extracted.items() if v not in (None, "", "None")}
    print(f"variables collected : {len(filled)} {json.dumps(filled, ensure_ascii=False)[:120]}")

    # 6. Anything the pipeline itself complained about.
    error = run.get("pipeline_error")
    print(f"pipeline error      : {error.get('detail')[:80] if error else 'none'}")

    # 4. How it felt to the caller.
    latencies = sorted(
        e["payload"]["latency_seconds"] * 1000
        for e in events
        if e.get("type") == "rtf-latency-measured"
        and isinstance(e.get("payload", {}).get("latency_seconds"), (int, float))
    )
    if latencies:
        slow = sum(1 for v in latencies if v > 2000)
        print(
            f"reply latency       : median {statistics.median(latencies):.0f} ms, "
            f"worst {latencies[-1]:.0f} ms, {slow}/{len(latencies)} over 2s"
        )

    # 3. Was the transcriber sure what language it was hearing?
    url = signed(run.get("transcript_url") or "")
    if not url:
        print("transcript          : not available")
        return
    transcript = urllib.request.urlopen(url, timeout=60).read().decode()
    caller_lines = [
        line.split("] user:", 1)[1] for line in transcript.splitlines() if "] user:" in line
    ]
    seen: set[str] = set()
    mixed_lines = 0
    for line in caller_lines:
        found = scripts_in(line)
        seen |= found
        if len(found) > 1:
            mixed_lines += 1
    print(f"caller scripts      : {sorted(seen) or ['latin only']}")
    if len(seen) > 1:
        print(f"  >> {len(seen)} scripts from one caller"
              f"{f', {mixed_lines} within a single utterance' if mixed_lines else ''}.")
        print("     Suspect the transcriber auto-detecting language per utterance.")
    if "cyrillic" in seen or re.search(r"[Ѐ-ӿ]", transcript):
        print("  >> Cyrillic in an Indian call. The model is being fed noise.")

    print(f"\n--- transcript ({len(caller_lines)} caller turns) ---")
    print(transcript[:2000])


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    workflow_id = int(sys.argv[1])
    if len(sys.argv) > 2:
        diagnose(workflow_id, int(sys.argv[2]))
        return
    listing = api(f"/workflow/{workflow_id}/runs", limit=5)
    runs = listing if isinstance(listing, list) else (
        listing.get("items") or listing.get("runs") or listing.get("data") or []
    )
    for entry in runs[:3]:
        diagnose(workflow_id, entry["id"])


if __name__ == "__main__":
    main()
