"""Build the corpus the disposition eval reads: transcripts and their labels.

Reads a running instance over the public API with a workspace API key, so it
works against production without database access and without this machine
holding credentials for anything else.

    export DECIBYL_API_KEY=dcb_...
    python -m scripts.fetch_disposition_corpus --out corpus.json

Only runs that have **both** a transcript and a recorded outcome are kept: a
call with no transcript has nothing to classify, and one with no label has
nothing to compare against. Transcripts are cached on disk, so a second run
costs one listing call.

Nothing here writes to the instance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_BASE = "https://api.decibyl.ai/api/v1"
#: The listing endpoint's own ceiling; asking for more is a 422.
PAGE = 100


def get(url: str, key: str, timeout: int = 60) -> Any:
    request = urllib.request.Request(url, headers={"X-API-Key": key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_runs(base: str, key: str, pages: int) -> list[dict[str, Any]]:
    seen: dict[int, dict[str, Any]] = {}
    for page in range(1, pages + 1):
        payload = get(f"{base}/organizations/usage/runs?limit={PAGE}&page={page}", key)
        rows = payload.get("runs") if isinstance(payload, dict) else payload
        if not rows:
            break
        for row in rows:
            if isinstance(row, dict) and "id" in row:
                seen[row["id"]] = row
    return sorted(seen.values(), key=lambda r: r["id"])


def transcript_for(row: dict[str, Any], cache: Path, timeout: int) -> str | None:
    """The call's text, from disk if we have it. A public download link, so
    no key: the run already granted it when the transcript was published."""
    url = row.get("transcript_public_url")
    if not url:
        return None
    path = cache / f"{row['id']}.txt"
    if not path.exists():
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                path.write_bytes(response.read())
        except (urllib.error.URLError, OSError, TimeoutError):
            return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if text.strip() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base", default=os.environ.get("DECIBYL_BASE", DEFAULT_BASE))
    parser.add_argument("--cache", type=Path, default=Path("transcripts"))
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()

    key = os.environ.get("DECIBYL_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set DECIBYL_API_KEY to a workspace API key.")

    args.cache.mkdir(parents=True, exist_ok=True)
    runs = fetch_runs(args.base, key, args.pages)

    corpus: list[dict[str, Any]] = []
    no_transcript = no_label = unreadable = 0
    for row in runs:
        recorded = row.get("call_outcomes") or []
        if not row.get("transcript_public_url"):
            no_transcript += 1
            continue
        if not recorded:
            no_label += 1
            continue
        text = transcript_for(row, args.cache, args.timeout)
        if text is None:
            unreadable += 1
            continue
        corpus.append(
            {
                "id": row["id"],
                "mode": row.get("mode"),
                "workflow": row.get("workflow_name"),
                "seconds": row.get("call_duration_seconds"),
                "disposition": row.get("disposition"),
                "recorded": [str(code) for code in recorded],
                "transcript": text,
            }
        )

    args.out.write_text(json.dumps(corpus, indent=1), encoding="utf-8")
    labels = Counter(code for item in corpus for code in item["recorded"])
    print(f"{len(runs)} runs read, {len(corpus)} usable -> {args.out}")
    print(f"  skipped: {no_transcript} without a transcript, {no_label} without a "
          f"label, {unreadable} whose transcript would not download")
    print(f"  labels: {dict(labels.most_common())}")
    print(f"  modes:  {dict(Counter(item['mode'] for item in corpus).most_common())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
