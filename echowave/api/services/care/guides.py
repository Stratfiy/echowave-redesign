"""Step-by-step phone help, in plain words.

Each guide is a short list of steps; each step says one thing to do and,
for when it does not work, one other thing to try. Written for a person
who has never been shown: name the thing on screen by what it looks like,
one action per step, no jargon. Phones differ, so steps say "usually"
where they do and the alternative covers the common other layout.

Guides are content, not code: adding one needs no migration, and
``test_care_tech_help`` checks every guide has steps, keywords and plain
lengths so a broken one cannot ship.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    say: str
    #: What to try if this step did not work.
    instead: str | None = None


@dataclass(frozen=True)
class Guide:
    slug: str
    title: str
    keywords: tuple[str, ...]
    steps: tuple[Step, ...]
    tags: tuple[str, ...] = field(default_factory=tuple)


GUIDES: tuple[Guide, ...] = (
    Guide(
        "bigger_text",
        "Make the writing on my phone bigger",
        (
            "bigger",
            "big",
            "text",
            "font",
            "writing",
            "letters",
            "small",
            "read",
            "size",
        ),
        (
            Step(
                "Find the Settings app. It usually looks like a grey gear wheel. Tap it.",
                "Swipe down from the top of the screen and tap the small gear wheel there.",
            ),
            Step(
                "Tap Display. On some phones it is called Display and brightness.",
                "Tap the search bar at the top of Settings and type: font size.",
            ),
            Step(
                "Tap Font size, or Text size.",
                "Look for Accessibility, tap it, then look for Font size there.",
            ),
            Step(
                "Move the slider to the right until the writing is easy to read.",
                "Tap the bigger A at the end of the slider a few times.",
            ),
        ),
    ),
    Guide(
        "louder_ring",
        "Make my phone ring louder",
        ("ring", "louder", "loud", "volume", "hear", "sound", "ringtone", "quiet"),
        (
            Step(
                "Press the button on the side of the phone that sits higher up. "
                "That turns the sound up.",
                "If nothing shows on screen, the phone may be locked. Press the "
                "power button once, then try the upper side button again.",
            ),
            Step(
                "A bar appears on the screen. Tap the three small dots or the small "
                "arrow beside it.",
                "Open Settings, then tap Sound and vibration.",
            ),
            Step(
                "Find Ringtone, and slide it all the way to the right.",
                "Make sure Do not disturb is off: swipe down from the top and tap "
                "Do not disturb if it is lit up.",
            ),
        ),
    ),
    Guide(
        "whatsapp_video_call",
        "Make a WhatsApp video call",
        ("whatsapp", "video", "call", "see", "face", "camera"),
        (
            Step(
                "Open WhatsApp. It is the green circle with a white phone inside.",
                "Swipe up from the bottom of the screen to see all your apps, and "
                "look for WhatsApp there.",
            ),
            Step(
                "Tap the name of the person you want to call.",
                "Tap the magnifying glass at the top and type their name.",
            ),
            Step(
                "At the top of the chat, tap the small video camera picture.",
                "If you only see a phone picture, tap the three dots at the top "
                "right first.",
            ),
            Step(
                "Wait for them to answer. Hold the phone in front of your face so "
                "they can see you.",
                "If it does not ring, check you are connected to Wi-Fi or mobile data.",
            ),
        ),
    ),
    Guide(
        "whatsapp_photo",
        "Send a photo on WhatsApp",
        ("photo", "picture", "image", "send", "whatsapp", "share", "pic"),
        (
            Step(
                "Open WhatsApp and tap the name of the person you want to send it to.",
                "Tap the magnifying glass at the top and type their name.",
            ),
            Step(
                "At the bottom, beside where you type, tap the small paper clip or the plus sign.",
                "Tap the small camera picture beside where you type instead.",
            ),
            Step(
                "Tap Gallery, or Photos, and tap the photo you want.",
                "If you see no photos, tap Allow when the phone asks for permission.",
            ),
            Step(
                "Tap the green arrow at the bottom right to send it.",
                "If it is still sending, wait a moment. A small clock means it is on its way.",
            ),
        ),
    ),
    Guide(
        "wifi",
        "Connect to Wi-Fi",
        ("wifi", "wi-fi", "internet", "connect", "network", "net", "password"),
        (
            Step(
                "Swipe down from the very top of the screen.",
                "Open the Settings app, the grey gear wheel, instead.",
            ),
            Step(
                "Press and hold the Wi-Fi picture. It looks like a fan of curved lines.",
                "In Settings, tap Wi-Fi, or Network and internet, then Wi-Fi.",
            ),
            Step(
                "Make sure Wi-Fi is switched on, then tap the name of your home Wi-Fi.",
                "If you do not see your Wi-Fi's name, move closer to the Wi-Fi box and wait.",
            ),
            Step(
                "Type the Wi-Fi password, written on a sticker on the Wi-Fi box at home, "
                "and tap Connect. This is the only password you ever type here; "
                "never give it to someone who calls you.",
                "Check capital and small letters carefully, and try again.",
            ),
        ),
    ),
    Guide(
        "block_number",
        "Stop calls from a number",
        ("block", "stop", "calls", "spam", "number", "annoying", "unknown", "fraud"),
        (
            Step(
                "Open the Phone app, where you make calls, and tap Recents or Call log.",
                "Tap the clock picture at the bottom of the Phone app.",
            ),
            Step(
                "Press and hold the number you want to stop.",
                "Tap the small i or the arrow beside the number instead.",
            ),
            Step(
                "Tap Block, or Block and report spam. Tap Block again to confirm.",
                "Tap the three dots at the top right, and look for Block there.",
            ),
        ),
    ),
    Guide(
        "screenshot",
        "Take a picture of my screen",
        ("screenshot", "screen", "capture", "picture of screen", "save screen"),
        (
            Step(
                "Press the power button and the lower volume button at the same "
                "time, and let go straight away.",
                "Swipe down from the top and look for Screenshot or Screen capture.",
            ),
            Step(
                "The screen flashes. The picture is now in your Gallery or Photos.",
                "Try again, pressing both buttons at exactly the same moment.",
            ),
        ),
    ),
    Guide(
        "torch",
        "Turn the torch on or off",
        ("torch", "flashlight", "light", "flash"),
        (
            Step(
                "Swipe down from the very top of the screen.",
                "Swipe down a second time to see more buttons.",
            ),
            Step(
                "Tap Torch, or Flashlight. Tap it again to turn it off.",
                "Swipe left on the buttons to find it on the next page.",
            ),
        ),
    ),
)

BY_SLUG = {g.slug: g for g in GUIDES}


def match(question: str) -> list[Guide]:
    """Guides for a question, best first; empty when none fits."""
    words = {w.strip(".,?!'\"").lower() for w in (question or "").split()}
    text = (question or "").lower()
    scored = []
    for guide in GUIDES:
        score = sum(1 for k in guide.keywords if k in words or (" " in k and k in text))
        if score:
            scored.append((score, guide))
    scored.sort(key=lambda pair: -pair[0])
    return [g for _, g in scored]
