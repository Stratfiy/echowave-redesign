"""Does Laya classify a finished call better than the model we use today?

The Jev assessment asked for this shape of evidence before any decision
service goes near Activity: run the candidate beside what ships, on our own
records, and count. Laya (Apache 2.0, convaiinnovations) answers typed
questions in one forward pass with no generation, which is exactly what
``disposition_run.classify_call`` asks a generative model to do today --
pick from a fixed taxonomy and return JSON we then have to parse.

What this measures, on transcripts we already hold:

- **Agreement.** Laya's top choice against the labels the shipped classifier
  recorded. Those labels are not ground truth; they are what we have. So
  agreement is read as "would this change the record", not "is this right".
- **The unclear pile.** A third of recent calls came back ``unclear``. For
  each, what Laya says instead and how sure it is. This is the number that
  decides whether Laya is worth anything to us: rescuing calls nobody could
  read is the whole case.
- **Routing.** Which checkpoint each transcript went to. Laya's English
  checkpoint is confidently wrong off Latin script (Khmer 0.000 at 95.2%
  confidence, by its authors' own table), and our calls are Hindi, Tamil and
  English inside one conversation. A transcript that routes to ``english``
  when it is not is the failure mode to find here, not in production.
- **Calibration.** Confidence against agreement, since the authors say both
  checkpoints ship over-confident. If a floor separates the agreements from
  the disagreements, that floor is how we would use it: below it, ``unclear``.
- **Latency on CPU**, because the box that would run this has no GPU.

Nothing here writes. It reads a corpus file and prints a table.

    python -m scripts.eval_laya_dispositions --corpus corpus.json
    python -m scripts.eval_laya_dispositions --corpus corpus.json --model multilingual

The corpus is a JSON list of objects with ``id``, ``transcript`` and
``recorded`` (the list of codes the shipped classifier stored), optionally
``mode``, ``workflow`` and ``seconds``. ``scripts/fetch_disposition_corpus.py``
builds one from a running instance.

Laya is not a dependency of this repo and must not become one on the strength
of a script. Install it in a throwaway environment: ``pip install laya``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

# The taxonomy the product ships, as `disposition.DEFAULT_DISPOSITIONS`
# states it. Duplicated rather than imported so the script runs in an
# environment that has laya and torch but not the api package and its
# database settings.
TAXONOMY: dict[str, str] = {
    "booked": "An appointment, demo or visit is confirmed",
    "interested": "Wants it, but committed to nothing yet",
    "callback": "Asked to be contacted at another time",
    "not_interested": "A clear no",
    "wrong_number": "Not the person we were trying to reach",
    "no_answer": "Nobody picked up, or picked up and said nothing",
    "unreachable": "Invalid, switched off, or could not connect",
    "unclear": "The conversation did not settle either way",
}

UNCLEAR = "unclear"

QUESTION: dict[str, Any] = {
    "outcome": {
        "type": "choice",
        "instructions": "What did this call achieve?",
        "criteria": dict(TAXONOMY),
    }
}


def percent(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.0f}%" if whole else "--"


def load_corpus(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    kept = [
        row
        for row in rows
        if str(row.get("transcript") or "").strip() and row.get("recorded")
    ]
    if not kept:
        raise SystemExit(f"{path}: nothing with both a transcript and a recorded label.")
    return kept


def classify(router: Any, transcript: str, model: str | None) -> dict[str, Any]:
    """One call, timed. A failure is a row too: a classifier that throws on
    real input is a classifier that would have left the call unlabelled."""
    started = time.perf_counter()
    try:
        result = router.predict(transcript, QUESTION, model=model)
    except Exception as exc:  # noqa: BLE001 - the eval reports, never raises
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}"[:200],
            "ms": (time.perf_counter() - started) * 1000,
        }
    ms = (time.perf_counter() - started) * 1000
    answer = (result.get("answers") or {}).get("outcome") or {}
    routing = result.get("routing") or {}
    detection = routing.get("detection") or {}
    return {
        "ok": True,
        "ms": ms,
        "choice": answer.get("choice"),
        "confidence": float(answer.get("confidence") or 0.0),
        "probabilities": answer.get("probabilities") or {},
        "checkpoint": routing.get("model"),
        "script": detection.get("script"),
        "language": detection.get("language"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--model",
        default=None,
        help="Pin a checkpoint (english, multilingual, typed-decisions). "
        "Omit to let Laya's router choose, which is what production would do.",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--tail",
        type=int,
        default=0,
        help="Send only the last N characters. What a call achieved is said at "
        "the end of it, and both checkpoints have a short context, so feeding "
        "the opening is a way to lose the answer before the model sees it.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    if args.limit:
        corpus = corpus[: args.limit]

    try:
        from laya import Router
    except ImportError:
        raise SystemExit(
            "laya is not installed here, deliberately. Make a throwaway "
            "environment and `pip install laya` into it."
        )

    router = Router(device=args.device, default="multilingual")

    rows: list[dict[str, Any]] = []
    for item in corpus:
        text = item["transcript"]
        if args.tail and len(text) > args.tail:
            text = text[-args.tail :]
        out = classify(router, text, args.model)
        recorded = [str(code) for code in item.get("recorded") or []]
        out["id"] = item.get("id")
        out["mode"] = item.get("mode")
        out["recorded"] = recorded
        # The shipped labels are a set and Laya answers with one. "Agrees"
        # means its answer is among them -- the weaker test, and the honest
        # one, because a call that is both booked and interested is not a
        # disagreement when Laya says booked.
        out["agrees"] = bool(out.get("ok")) and out.get("choice") in recorded
        out["was_unclear"] = recorded == [UNCLEAR]
        rows.append(out)

    good = [r for r in rows if r["ok"]]
    failed = [r for r in rows if not r["ok"]]
    print(f"\nLaya against the shipped classifier, {len(rows)} calls")
    print("=" * 64)
    if failed:
        print(f"\n{len(failed)} call(s) threw:")
        for row in failed[:5]:
            print(f"  {row['id']}: {row['error']}")

    # --- agreement, excluding the unclear pile, which is its own question ---
    scored = [r for r in good if not r["was_unclear"]]
    agreed = [r for r in scored if r["agrees"]]
    print(f"\nWhere the shipped classifier named an outcome ({len(scored)} calls)")
    print(f"  Laya agrees on          {len(agreed):>3}  ({percent(len(agreed), len(scored))})")
    disagreed = [r for r in scored if not r["agrees"]]
    said_unclear = [r for r in disagreed if r["choice"] == UNCLEAR]
    print(f"  Laya says unclear       {len(said_unclear):>3}  (we had a label)")
    print(f"  Laya says something else{len(disagreed) - len(said_unclear):>3}")
    if disagreed:
        print("\n  Disagreements:")
        for row in disagreed[:12]:
            print(
                f"    run {row['id']:>4}  recorded {','.join(row['recorded']):<24}"
                f" laya {str(row['choice']):<16} conf {row['confidence']:.2f}"
            )

    # --- the number that makes the agreement rate mean anything ---
    #
    # An accuracy with no baseline beside it is a number that can be read any
    # way the reader likes. Two are worth having: chance over the taxonomy,
    # and the laziest strategy there is -- answer the commonest label every
    # time, having read nothing. A classifier that cannot beat the lazy one
    # is not classifying.
    if scored:
        print("\nWhat it has to beat")
        per_label = Counter(code for row in scored for code in row["recorded"])
        for code, hits in per_label.most_common(3):
            print(
                f'  always answer "{code}"'.ljust(34)
                + f"{hits:>3} of {len(scored)}  ({percent(hits, len(scored))})"
            )
        chance = len(scored) / max(len(TAXONOMY), 1)
        print(f"  chance over {len(TAXONOMY)} labels".ljust(34)
              + f"{chance:>5.1f} of {len(scored)}  ({percent(round(chance), len(scored))})")
        print(f"  Laya".ljust(34) + f"{len(agreed):>3} of {len(scored)}  "
              f"({percent(len(agreed), len(scored))})")

    # --- the pile this exists for ---
    pile = [r for r in good if r["was_unclear"]]
    named = [r for r in pile if r["choice"] != UNCLEAR]
    print(f"\nWhere the shipped classifier gave up ({len(pile)} calls marked unclear)")
    print(f"  Laya names an outcome   {len(named):>3}  ({percent(len(named), len(pile))})")
    print(f"  Laya also says unclear  {len(pile) - len(named):>3}")
    if named:
        print("\n  What it named, and how sure:")
        for row in sorted(named, key=lambda r: -r["confidence"]):
            print(
                f"    run {row['id']:>4}  laya {str(row['choice']):<16}"
                f" conf {row['confidence']:.2f}  [{row['checkpoint']}/{row['script']}]"
            )

    # --- routing, the failure mode worth finding here ---
    print("\nRouting")
    for checkpoint, count in Counter(r["checkpoint"] for r in good).most_common():
        print(f"  {str(checkpoint):<16} {count:>3}")
    scripts = Counter(f"{r['script']}/{r['language']}" for r in good)
    print("  scripts seen:", dict(scripts.most_common(6)))
    latin_english = [
        r for r in good if r["script"] == "latin" and r["checkpoint"] == "english"
    ]
    print(
        f"  {len(latin_english)} call(s) routed to the English checkpoint on Latin "
        "script. Hinglish is Latin script and is not English, so this is the "
        "number to look at by hand."
    )

    # --- calibration: is there a floor that separates right from wrong? ---
    if scored:
        right = [r["confidence"] for r in scored if r["agrees"]]
        wrong = [r["confidence"] for r in scored if not r["agrees"]]
        print("\nConfidence")
        if right:
            print(f"  when it agrees     median {statistics.median(right):.2f}  n={len(right)}")
        if wrong:
            print(f"  when it disagrees  median {statistics.median(wrong):.2f}  n={len(wrong)}")
        for floor in (0.5, 0.6, 0.7, 0.8, 0.9):
            kept = [r for r in scored if r["confidence"] >= floor]
            kept_right = [r for r in kept if r["agrees"]]
            print(
                f"  floor {floor:.1f}: keeps {len(kept):>3} of {len(scored)}, "
                f"agreement {percent(len(kept_right), len(kept))}"
            )

    times = [r["ms"] for r in good]
    if times:
        times.sort()
        print("\nLatency on this machine, CPU")
        print(f"  median {statistics.median(times):.0f} ms   slowest {times[-1]:.0f} ms")

    if args.json_out:
        args.json_out.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
        print(f"\nPer-call rows written to {args.json_out}")

    print(
        "\nRead this as 'would Laya change the record', not 'is Laya right'. "
        "The shipped labels are a generative model's, not a person's."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
