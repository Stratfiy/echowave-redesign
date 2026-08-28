SARVAM_TTS_MODELS = ("bulbul:v2", "bulbul:v3")
SARVAM_V2_VOICES = (
    "anushka",
    "manisha",
    "vidya",
    "arya",
    "abhilash",
    "karun",
    "hitesh",
)
SARVAM_V3_VOICES = (
    "shubh",
    "aditya",
    "ritu",
    "priya",
    "neha",
    "rahul",
    "pooja",
    "rohan",
    "simran",
    "kavya",
    "amit",
    "dev",
    "ishita",
    "shreya",
    "ratan",
    "varun",
    "manan",
    "sumit",
    "roopa",
    "kabir",
    "aayan",
    "ashutosh",
    "advait",
    "amelia",
    "sophia",
    "anand",
    "tanya",
    "tarun",
    "sunny",
    "mani",
    "gokul",
    "vijay",
    "shruti",
    "suhani",
    "mohit",
    "kavitha",
    "rehan",
    "soham",
    "rupali",
)
SARVAM_LANGUAGES = (
    "bn-IN",
    "en-IN",
    "gu-IN",
    "hi-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
    "as-IN",
)
SARVAM_STT_MODELS = ("saarika:v2.5", "saaras:v3")
# saarika:v2.5 language codes (unknown = auto-detect)
SARVAM_STT_LANGUAGES_V25 = (
    "unknown",
    "hi-IN",
    "bn-IN",
    "gu-IN",
    "kn-IN",
    "ml-IN",
    "mr-IN",
    "od-IN",
    "pa-IN",
    "ta-IN",
    "te-IN",
    "en-IN",
)
# saaras:v3 adds these regional languages on top of the v2.5 set. Full list: https://docs.sarvam.ai/api-reference-docs/speech-to-text/transcribe
SARVAM_STT_LANGUAGES_V3 = SARVAM_STT_LANGUAGES_V25 + (
    "as-IN",
    "ur-IN",
    "ne-IN",
    "kok-IN",
    "ks-IN",
    "sd-IN",
    "sa-IN",
    "sat-IN",
    "mni-IN",
    "brx-IN",
    "mai-IN",
    "doi-IN",
)
# sarvam-30b is retired: the API answers a 400 with "Model 'sarvam-30b' has
# been deprecated. Please use one of the available models instead:
# sarvam-105b." Offering it meant every agent built on the default died mid-call,
# after the caller had already spoken.
#
# Conversations first, and it is the only one that belongs on a phone line.
# sarvam-105b is a reasoning model: it emits chain-of-thought before any answer
# on every request, cannot be told not to (reasoning_effort takes only
# low/medium/high, and low measured slower than medium), and cost 6,045ms of a
# 7,554ms turn on run 77. The conversational variant does no reasoning, reaches
# its first content token in 0.24s, and still emits tool_calls -- which the
# workflow engine requires, because every node transition is a function call.
#
# sarvam-105b is kept selectable rather than removed: it is a legitimate choice
# for text chat, where several seconds of reasoning buys quality nobody hears.
SARVAM_LLM_MODELS = ("sarvam-105b-conversations", "sarvam-105b")
