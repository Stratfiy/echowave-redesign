"""Speak the way the caller does, rather than in one pure language.

Nobody in urban India speaks the language a language model reaches for by
default. Ask for Hindi and you get news-bulletin Hindi — "आपकी नियुक्ति
निर्धारित की गई है" for what every actual person says as "aapka appointment
book ho gaya hai". The Sanskritised word is not more polite; on a phone call it
is less understood, and it announces within one sentence that this is not a
person.

The other half of the same problem is the model trying to be helpful. Told the
caller speaks Hindi, it translates *everything* into Hindi, including the words
that have no everyday Hindi form — appointment, booking, delivery, OTP, EMI,
report, doctor. Nobody is waiting for their "औषधालय"; they are waiting for
their report.

So this is not a translation instruction, it is a register instruction: use the
words people use, in the mix they use them, in the script they would type them
in. It is off unless asked for, because it is added to the operator's own
prompt and an operator who wrote "reply only in formal Hindi" meant it.

Deliberately no list of approved English words. A fixed vocabulary would be
wrong within a month and wrong per region on day one — Bengaluru mixes English
into Kannada differently from how Jaipur mixes it into Hindi — and the model
already knows how people talk. What it needs is permission, which is what this
gives it.
"""

from __future__ import annotations

from typing import Any

#: The per-agent key in ``workflow_configurations``.
CONFIG_KEY = "speak_like_callers"

CODE_MIXED_INSTRUCTIONS = """\
HOW TO SPEAK:
- Talk the way people actually talk on the phone here, not the way a language
  is written formally. Mixing English words into the local language is normal
  speech, not a mistake to correct.
- Mirror the caller. If they mix English into Hindi, Tamil, Telugu, Kannada,
  Marathi, Bengali or any other language, mix it the same way and to about the
  same degree. If they speak one language purely, do the same.
- Keep the word people use. Appointment, booking, order, delivery, report,
  doctor, payment, OTP, EMI, invoice, address and the like stay in English even
  mid-sentence, because the formal translation is not what anyone says and is
  often not understood.
- Numbers, dates, times, amounts, names, model numbers and technical terms stay
  in whatever form the caller used them in.
- Write the local language in its own script, and the English words in
  Latin script, exactly as a mixed sentence is spoken: "உங்க appointment
  confirm ஆயிடுச்சு". The voice reads each script the way it should be read;
  a Tamil sentence typed out in Latin letters is read as English and comes
  out mangled.
- Never comment on the language, apologise for switching, or ask which one to
  use. Just answer in the one being spoken.
- Do not be more formal than the caller. Match their register, including how
  short their sentences are."""


def wants_code_mixed_speech(run_configs: Any) -> bool:
    """Has this agent been asked to speak the way its callers do?

    Off unless turned on: this text is appended to the operator's own prompt,
    and an operator who wrote "reply only in formal Hindi" meant that.
    """
    if not isinstance(run_configs, dict):
        return False
    return bool(run_configs.get(CONFIG_KEY))
