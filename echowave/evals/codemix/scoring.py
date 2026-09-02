"""Scoring for code-mixed speech, where word error rate alone is misleading.

The existing STT benchmark captures what a provider heard. This scores it
against what was said — and it does so with two numbers rather than one,
because on Indian calls the single number lies in a specific direction.

**Why WER alone is not enough.** Take a Bengaluru caller saying "मेरा order
kahan hai, delivery date confirm karo". Roughly half those tokens are English.
A transcriber trained mostly on English will get the English half and drop or
mangle the Hindi half — and still score a WER around 0.4, which looks like a
mediocre transcript rather than a broken call. It is a broken call: the words
carrying the intent are the ones that were lost.

So this reports **code-switch recall** next to WER: of the tokens in the
non-Latin script, how many survived. That number goes to nearly zero on an
English-first model while WER stays respectable, which is exactly the failure
the market's "we support 100+ languages" claims hide. It is also the number a
demo should be judged on, because it is the one a customer feels.

Tanglish and Hinglish differ in a way that matters here. Hindi in a code-mixed
sentence is usually typed and transcribed in Devanagari, so script alone
separates the two languages. Tamil is very often *romanised* — "enna panra"
rather than "என்ன பண்ற" — so script detection finds nothing and the recall
metric would silently read 1.0 on a transcript that lost every Tamil word.
That is why a corpus entry carries an explicit list of the tokens that must
survive rather than relying on script detection alone.

Nothing here calls a vendor. Scoring is pure and tested; the provider calls
live in the benchmark that feeds it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

#: Unicode blocks for the Indic scripts this platform serves. Used only as a
#: fallback when a corpus entry does not name its own must-survive tokens.
_INDIC_RANGES = (
    (0x0900, 0x097F),  # Devanagari — Hindi, Marathi
    (0x0980, 0x09FF),  # Bengali, Assamese
    (0x0A00, 0x0A7F),  # Gurmukhi — Punjabi
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0B00, 0x0B7F),  # Odia
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0C80, 0x0CFF),  # Kannada
    (0x0D00, 0x0D7F),  # Malayalam
)

_WHITESPACE = re.compile(r"\s+")


def _strip_punctuation(text: str) -> str:
    r"""Remove punctuation and symbols, keeping every letter, digit and mark.

    Written as a category filter rather than the obvious ``[^\w\s]`` because
    that expression is wrong for every script this file exists to measure.
    Devanagari and Tamil write vowels as combining marks — the ा in मेरा, the
    ் in என்ன — and those are Unicode category ``Mn``, which ``\w`` does not
    match. The regex therefore treated them as punctuation and replaced them
    with spaces, shredding मेरा into two fragments that matched nothing.

    Every Indic score would have been quietly wrong, in the direction of
    reporting that the transcriber lost words it had actually heard. Worth the
    slower per-character loop.
    """
    return "".join(
        " " if unicodedata.category(ch).startswith(("P", "S")) else ch
        for ch in text
    )


def is_indic(token: str) -> bool:
    """Does this token contain a character in an Indic script?

    One character is enough. A token is not split across scripts in practice,
    and requiring all characters would drop tokens carrying a Latin digit.
    """
    return any(
        any(start <= ord(ch) <= end for start, end in _INDIC_RANGES) for ch in token
    )


def normalise(text: str) -> list[str]:
    """Text to comparable tokens.

    **NFD, not NFC.** Devanagari and Tamil both admit more than one encoding of
    the same visible string, and two spellings of one word must not score as an
    error. The obvious choice is NFC, and it is wrong here: the nukta
    consonants — क़ ख़ ग़ ज़ ड़ ढ़ फ़ — are Unicode *composition exclusions*, so
    NFC deliberately does not recompose क + ़ back into क़. Under NFC the two
    forms stay different and a transcriber returning the decomposed spelling is
    marked wrong for a difference nobody can see. NFD takes both apart to the
    same sequence, which is what comparison actually needs.

    Case folding and punctuation removal follow the usual WER convention: a
    transcriber is not being marked on commas.
    """
    text = unicodedata.normalize("NFD", text or "")
    text = _strip_punctuation(text.casefold())
    return [t for t in _WHITESPACE.split(text) if t]


def _levenshtein(reference: list[str], hypothesis: list[str]) -> int:
    """Edit distance in tokens. Two rows rather than a full matrix — the
    corpus is short utterances, but this is called once per provider per
    language per run and there is no reason to hold the grid."""
    if not reference:
        return len(hypothesis)

    previous = list(range(len(reference) + 1))
    for j, hyp in enumerate(hypothesis, start=1):
        current = [j]
        for i, ref in enumerate(reference, start=1):
            current.append(
                previous[i - 1] if ref == hyp
                else 1 + min(previous[i], current[i - 1], previous[i - 1])
            )
        previous = current
    return previous[-1]


@dataclass(frozen=True)
class Score:
    """One utterance, one provider.

    Attributes:
        wer: Word error rate. 0.0 is perfect; above 1.0 is possible when the
            transcriber inserts more than it gets right, and is not clamped —
            a hallucinating provider should look worse than a silent one.
        code_switch_recall: Of the tokens that had to survive, the fraction
            that did. ``None`` when the utterance is monolingual and there were
            none to check, which is different from zero and must not be
            averaged in as though it were.
        reference_tokens: Token count, so a run can weight by length rather
            than treating a two-word utterance as equal to a twenty-word one.
        missing: The must-survive tokens that did not appear. The useful half
            of the output when reading a failure — it names the words the
            caller lost.
    """

    wer: float
    code_switch_recall: float | None
    reference_tokens: int
    missing: tuple[str, ...]


def score(
    reference: str,
    hypothesis: str,
    *,
    must_survive: list[str] | None = None,
) -> Score:
    """Score one transcript against what was actually said.

    ``must_survive`` names the tokens carrying the utterance's meaning across
    the language boundary — required for romanised Tamil, where script
    detection finds nothing. Left unset, it falls back to every Indic-script
    token in the reference, which is correct for Devanagari Hinglish and
    silently vacuous for romanised Tanglish. The corpus sets it explicitly for
    that reason.

    Recall is checked as *set membership*, not position: a provider that heard
    every meaningful word but ordered them differently has not lost the call.
    Ordering is already penalised by WER, and counting it twice would make the
    two numbers say the same thing.
    """
    ref_tokens = normalise(reference)
    hyp_tokens = normalise(hypothesis)

    wer = (
        _levenshtein(ref_tokens, hyp_tokens) / len(ref_tokens)
        if ref_tokens
        else (0.0 if not hyp_tokens else float(len(hyp_tokens)))
    )

    if must_survive is None:
        required = [t for t in ref_tokens if is_indic(t)]
    else:
        required = [t for tok in must_survive for t in normalise(tok)]

    if not required:
        return Score(wer, None, len(ref_tokens), ())

    heard = set(hyp_tokens)
    missing = tuple(t for t in required if t not in heard)
    recall = (len(required) - len(missing)) / len(required)

    return Score(wer, recall, len(ref_tokens), missing)


def aggregate(scores: list[Score]) -> dict[str, float | int | None]:
    """Roll a provider's utterances into the numbers that go on a slide.

    WER is weighted by reference length, which is the standard definition and
    stops a two-word utterance counting as much as a twenty-word one. Recall
    is averaged over only the utterances that had something to recall — a
    monolingual line contributes nothing rather than a free 1.0, which would
    let a provider raise its code-switching score by transcribing English.
    """
    if not scores:
        return {"utterances": 0, "wer": None, "code_switch_recall": None}

    total_tokens = sum(s.reference_tokens for s in scores)
    weighted_errors = sum(s.wer * s.reference_tokens for s in scores)

    recalls = [s.code_switch_recall for s in scores if s.code_switch_recall is not None]

    return {
        "utterances": len(scores),
        "wer": (weighted_errors / total_tokens) if total_tokens else None,
        "code_switch_recall": (sum(recalls) / len(recalls)) if recalls else None,
        "code_switched_utterances": len(recalls),
    }
