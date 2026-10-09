"""Labelled phrases for escalation v2's deterministic reading (services/escalation/signals.py).

Every change to the patterns is measured against these. Each set has
positives and negatives, in English, Hindi (Devanagari) and the Hinglish
callers actually use, including the over-escalations found by verification
("I'll speak to someone at home", "transfer me the refund", "aap log fraud
ho"). Add a phrase here before changing a pattern for it.

Not a test module itself (no ``test_`` prefix): ``test_escalation_followups``
and ``test_escalation_simulated_callers`` read it.
"""

from __future__ import annotations

#: (what the caller said, "strong" | "weak" | None)
HUMAN_REQUEST: list[tuple[str, str | None]] = [
    # --- asks for a person: act at once -----------------------------------
    ("Let me talk to a human please", "strong"),
    ("Can I speak to someone?", "strong"),
    ("connect me to your manager", "strong"),
    ("I don't want to talk to a bot", "strong"),
    ("I want a real person, not a machine", "strong"),
    ("Connect me to customer care", "strong"),
    ("put me through to a representative", "strong"),
    ("I'd like to speak with a representative", "strong"),
    ("May I talk to the manager?", "strong"),
    ("Speak to a manager.", "strong"),
    ("Umm, haan, speak to a manager", "strong"),
    ("Please transfer me.", "strong"),
    ("transfer me ji", "strong"),
    ("Can you transfer me to an agent", "strong"),
    ("I need to speak to someone urgently", "strong"),
    ("get me a supervisor", "strong"),
    ("mujhe kisi insaan se baat karni hai", "strong"),
    ("manager se baat karao abhi", "strong"),
    ("bas, manager se baat karao", "strong"),
    ("koi insaan se baat karwao please", "strong"),
    ("मुझे मैनेजर से बात करनी है", "strong"),
    ("Yaar please connect me to customer care, kuch samajh nahi aa raha", "strong"),
    # --- mentions a person: once is not a request -------------------------
    ("Are you a robot?", "weak"),
    ("Is there a real person there?", "weak"),
    ("Is this customer care?", "weak"),
    ("kya aap insaan ho?", "weak"),
    # --- neither ----------------------------------------------------------
    ("I'll speak to someone at home and call you back", None),
    ("I need to speak to someone at home first", None),
    ("Can you transfer me the refund to my UPI", None),
    ("transfer me the money today", None),
    ("I need to check with my manager before I order", None),
    ("My manager asked me to call about the bulk order", None),
    ("OK. My manager wants the receipt emailed", None),
    ("I need to talk to my wife about it", None),
    ("I don't want to talk to a human, just book it", None),
    ("Can someone help me with my order?", None),
    ("I spoke to a person yesterday about this", None),
    ("I want to know if a person can visit on Monday", None),
    ("What time do you open tomorrow?", None),
    ("Can we speak in Hindi?", None),
    ("ghar mein kisi se baat karni hai pehle", None),
    ("mujhe apne manager se baat karni padegi", None),
    ("main ghar pe kisi se baat karke batata hoon", None),
    ("pehle papa se baat karni hai", None),
    ("अपने मैनेजर से बात करके बताता हूँ", None),
]

#: (what the caller said, the topic it must raise, or None for no topic).
#: Read with the default policy plus vulnerable_caller opted in.
TOPICS: list[tuple[str, str | None]] = [
    # --- fraud reported: the caller was defrauded --------------------------
    ("Someone used my card, I think I've been scammed", "fraud"),
    ("I shared my OTP with a caller and money went", "fraud"),
    ("mere saath dhokha hua hai, paise kat gaye", "fraud"),
    ("There's an unauthorized transaction on my account", "fraud"),
    ("I got a phishing message pretending to be you", "fraud"),
    ("Someone used my card, this is a fraud", "fraud"),
    ("you people are frauds, someone used my card", "fraud"),
    # --- the business called a fraud: an angry customer --------------------
    ("Aap log fraud ho, my cake never came", None),
    ("you guys are a total scam, the order is late again", None),
    ("tum log chor ho, dhokebaaz, cake kharab tha", None),
    ("your company is a fraud company", None),
    # --- emergency (default on) -------------------------------------------
    ("My father has chest pain and can't breathe", "emergency"),
    ("mere papa ko chest pain ho raha hai", "emergency"),
    ("मेरे पिता बेहोश हो गए हैं", "emergency"),
    # --- legal threat (default on) ----------------------------------------
    ("I'm going to send you a legal notice", "legal_threat"),
    ("main consumer court jaunga", "legal_threat"),
    # --- vulnerable caller (opt in): distress, self-harm, alone and old ----
    ("I am 82 and live alone, I am confused about what I ordered", "vulnerable_caller"),
    ("I feel hopeless, I don't want to live anymore", "vulnerable_caller"),
    ("I want to hurt myself", "vulnerable_caller"),
    ("he beats me and I am scared", "vulnerable_caller"),
    ("mujhe jeene ka mann nahi karta", "vulnerable_caller"),
    ("मुझे बहुत डर लग रहा है, कोई मदद करने वाला नहीं", "vulnerable_caller"),
    # --- not vulnerable: ordinary confusion -------------------------------
    ("I'm confused about my bill", None),
    ("I live alone so deliver after 6", None),
    ("I am scared the cake will be late", None),
    # --- ordinary ----------------------------------------------------------
    ("What cakes do you have?", None),
    ("Kya bakwaas hai, nobody picks up and the cake was wrong", None),
]

#: The over-escalations verification found, by name; each must read as
#: nothing at all with the default policy.
REGRESSIONS: dict[str, str] = {
    "speak_to_someone_at_home": "I'll speak to someone at home and call you back",
    "transfer_me_the_refund": "Can you transfer me the refund to my UPI",
    "angry_you_are_fraud": "Aap log fraud ho, my cake never came",
    "need_to_check_with_manager": "I need to check with my manager before I order",
}
