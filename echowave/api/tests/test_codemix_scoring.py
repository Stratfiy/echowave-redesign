"""Scoring for code-mixed speech — the metric, not the vendors.

This is the part of the eval that has to be right, because every provider
comparison is read through it. It calls nothing external and runs in CI.

The claim the metric exists to support is narrow and worth stating: on Indian
calls, word error rate alone is misleading in a *known direction*. An
English-first transcriber gets the English half of a Hinglish sentence and
drops the Hindi half, scoring a mediocre WER on what is actually a broken
call — because the dropped words are the ones carrying the intent. Code-switch
recall is what separates those two cases, and the tests below are what make it
trustworthy enough to put in front of a customer.
"""

import pytest

from evals.codemix.corpus import CORPUS
from evals.codemix.scoring import aggregate, is_indic, normalise, score


class TestNormalisation:
    def test_case_and_punctuation_are_not_errors(self):
        assert normalise("Order, please!") == normalise("order please")

    def test_devanagari_survives_normalisation(self):
        assert normalise("मेरा order") == ["मेरा", "order"]

    def test_composed_and_decomposed_forms_are_one_token(self):
        """Devanagari and Tamil both admit more than one encoding of the same
        visible string. Without NFC, two spellings of one word score as an
        error and every provider looks worse than it is."""
        # U+0958 is a *composition exclusion*: NFC will not produce it, so the
        # composed form has to be written explicitly rather than round-tripped.
        composed = "\u0958"  # DEVANAGARI LETTER QA, single code point
        decomposed = "\u0915\u093c"  # KA + NUKTA, renders identically
        assert composed != decomposed
        assert normalise(composed) == normalise(decomposed)


class TestScriptDetection:
    @pytest.mark.parametrize(
        "token", ["मेरा", "என்ன", "అలా", "ಮತ್ತು", "ਪੰਜਾਬੀ", "ગુજરાતી", "বাংলা"]
    )
    def test_indic_scripts_are_detected(self, token):
        assert is_indic(token)

    @pytest.mark.parametrize("token", ["order", "delivery", "560001", "enna"])
    def test_latin_and_digits_are_not(self, token):
        """`enna` is the important row: romanised Tamil is Latin script, which
        is exactly why the corpus must name its own must-survive tokens."""
        assert not is_indic(token)


class TestWordErrorRate:
    def test_a_perfect_transcript_scores_zero(self):
        assert score("मेरा order kahan hai", "मेरा order kahan hai").wer == 0.0

    def test_every_word_wrong_scores_one(self):
        assert score("one two three", "four five six").wer == 1.0

    def test_hallucination_is_allowed_past_one(self):
        """Not clamped, deliberately. A provider that invents ten words should
        rank below one that returns nothing, and clamping makes them equal."""
        assert score("hello", "a b c d e f").wer > 1.0

    def test_silence_against_speech_is_total_loss(self):
        assert score("मेरा order kahan hai", "").wer == 1.0


class TestCodeSwitchRecall:
    """The metric that carries the argument."""

    def test_dropping_the_hindi_half_is_visible_where_wer_hides_it(self):
        # An English-first transcriber on a Hinglish sentence: it gets every
        # English word and loses every Hindi one.
        reference = "मेरा order kahan hai delivery confirm karo"
        hypothesis = "order delivery confirm"

        result = score(reference, hypothesis)

        # WER alone reads as "mediocre", which understates a broken call...
        assert result.wer < 0.75
        # ...while recall says the Hindi was lost outright.
        assert result.code_switch_recall == 0.0
        assert "मेरा" in result.missing

    def test_keeping_the_hindi_scores_full_recall(self):
        result = score("मेरा order kahan hai", "मेरा order kahan hai")
        assert result.code_switch_recall == 1.0
        assert result.missing == ()

    def test_partial_recall_is_proportional(self):
        result = score("मेरा order कहाँ है", "मेरा order")
        assert result.code_switch_recall == pytest.approx(1 / 3)

    def test_a_monolingual_utterance_has_no_recall_rather_than_zero(self):
        """None, not 0.0. An English control has nothing to recall, and
        averaging a zero in would punish a provider for a line that never
        tested code-switching."""
        assert (
            score("check my order status", "check my order status").code_switch_recall
            is None
        )

    def test_romanised_tamil_needs_explicit_tokens(self):
        """Script detection finds nothing in `enna panra`. Without
        must_survive the metric reads a perfect score on a transcript that
        lost every Tamil word — the exact false negative this guards."""
        reference = "enna aachu my order"
        hypothesis = "my order"

        blind = score(reference, hypothesis)
        assert blind.code_switch_recall is None  # silently vacuous

        told = score(reference, hypothesis, must_survive=["enna", "aachu"])
        assert told.code_switch_recall == 0.0

    def test_recall_ignores_word_order(self):
        """Order is already penalised by WER. Counting it twice would make the
        two numbers say the same thing, and a provider that heard every
        meaningful word has not lost the call."""
        result = score("मेरा order kahan hai", "kahan hai order मेरा")
        assert result.code_switch_recall == 1.0
        assert result.wer > 0


class TestAggregate:
    def test_wer_is_weighted_by_length(self):
        """Unweighted, a two-word utterance would count as much as a twenty-
        word one and a provider could game the average on short lines."""
        long_perfect = score("a b c d e f g h i j", "a b c d e f g h i j")
        short_wrong = score("x y", "q r")

        rolled = aggregate([long_perfect, short_wrong])
        assert rolled["wer"] == pytest.approx(2 / 12)

    def test_monolingual_lines_do_not_inflate_recall(self):
        """A provider must not raise its code-switching score by transcribing
        English well."""
        mixed_failure = score("मेरा order", "order")
        english_control = score("my order please", "my order please")

        rolled = aggregate([mixed_failure, english_control])
        assert rolled["code_switch_recall"] == 0.0
        assert rolled["code_switched_utterances"] == 1

    def test_an_empty_run_reports_nothing_rather_than_zero(self):
        assert aggregate([])["wer"] is None


class TestTheCorpusItself:
    def test_ids_are_unique(self):
        ids = [u.id for u in CORPUS]
        assert len(ids) == len(set(ids))

    def test_every_romanised_entry_names_its_must_survive_tokens(self):
        """The failure this catches is silent: a romanised entry without them
        scores a perfect recall no matter what the transcriber returns."""
        romanised = [u for u in CORPUS if u.language in {"tanglish", "hinglish-roman"}]
        assert romanised
        for utterance in romanised:
            assert utterance.must_survive, utterance.id

    def test_must_survive_tokens_actually_appear_in_the_text(self):
        """A typo here would mark a token missing on every provider forever,
        and read as a universal failure rather than a corpus bug."""
        for utterance in CORPUS:
            tokens = set(normalise(utterance.text))
            for required in utterance.must_survive:
                assert set(normalise(required)) <= tokens, (utterance.id, required)

    def test_there_are_monolingual_controls(self):
        """Without them, a provider that scores badly on everything is
        indistinguishable from one that only fails at code-switching."""
        languages = {u.language for u in CORPUS}
        assert "english" in languages
        assert "hindi" in languages

    def test_devanagari_entries_need_no_explicit_tokens(self):
        """Script detection covers them; requiring the list anyway would be
        duplication that can fall out of sync with the text."""
        for utterance in CORPUS:
            if utterance.language == "hinglish":
                assert any(is_indic(t) for t in normalise(utterance.text)), utterance.id
