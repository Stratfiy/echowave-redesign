from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MAX_CALL_DURATION_SECONDS = 300
# Hard ceiling on configurable call duration. Must stay <= the concurrency
# rate limiter's stale_call_timeout (20 min): a call running past that has
# its slot purged as stale and the org concurrency limit under-counts.
MAX_CALL_DURATION_SECONDS = 1200
DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_SMART_TURN_STOP_SECS = 2.0
DEFAULT_TURN_START_STRATEGY = "default"
DEFAULT_TURN_START_MIN_WORDS = 3
DEFAULT_PROVISIONAL_VAD_PAUSE_SECS = 1.5
# "transcription", on measurement rather than on the argument below.
#
# The argument for "turn_analyzer" is good and it is still here because it may
# well be right on another stack: a local model asks whether the utterance
# *sounds* finished, so it is quick on a completed sentence and patient on an
# ambiguous one, where "transcription" waits a fixed timer either way.
#
# It is not what happens here. From `call_turn_metrics`, the endpointing stage
# (`t_endpoint_fired_ms - t_user_stopped_ms`) measures:
#
#     transcription    1171-1174ms, across 40 turns of 15 calls
#     turn_analyzer    1311-1312ms, runs 77 and 78
#
# Two things follow. The stage never cost the 600ms this comment used to claim
# — that figure was VAD stop_secs plus user_speech_timeout added up from
# reading the code, and the measured stage is ~1172ms whichever strategy runs.
# What actually dominates it is Sarvam STT finalisation: the turn is not
# released until the final transcript lands, and no turn-stop strategy can
# return time the STT has not finished spending. Against that floor the
# analyzer's own inference is pure addition, and it reads as a steady +139ms.
#
# So the real gain on this stage is not here. An STT that emits its own turn
# boundaries (Deepgram Flux, Cartesia ink-2) removes the wait rather than
# tuning it — see `_create_non_realtime_user_turn_stop_strategies`, where such
# a model skips this budget entirely.
#
# "turn_analyzer" stays selectable, and the dependency it needs is installed
# (api/Dockerfile carries pipecat's local-smart-turn-v3 extra). A workflow on a
# faster-finalising STT is exactly where it should win.
DEFAULT_TURN_STOP_STRATEGY = "transcription"
# How long the turn waits after the VAD reports silence, in case the caller was
# only drawing breath. Paid on every turn of the default "transcription"
# strategy, on top of the VAD's own 0.2s — so it sets a floor under perceived
# latency that no faster model downstream can recover.
#
# 0.4 rather than the pipecat library default of 0.6: two fifths of a second is
# still a real pause by the standards of a phone call, and the 200ms saved is
# larger than the entire time-to-first-byte of most speech synthesis. Raise it
# for a workflow whose callers read out numbers or think mid-sentence; lower it
# for short scripted confirmations.
DEFAULT_USER_SPEECH_TIMEOUT = 0.4
# Off by default, and off means the processor is never built into the pipeline
# at all -- an agent nobody configured this on runs exactly the frames it ran
# before. The pause only helps where an interruption was brief; everywhere else
# the caller finishing, the turn being detected, the model answering and speech
# being synthesised already take longer than any sensible value here.
DEFAULT_INTERRUPTION_BACKOFF_SECS = 0.0
DEFAULT_CONTEXT_COMPACTION_ENABLED = False


class FallbackServiceConfiguration(BaseModel):
    """One backup in an ordered chain, tried when the one before it fails.

    Deliberately thin. A backup is chosen to keep a live call alive, not to be
    tuned -- so it names a provider and, where the provider needs them, a model
    and a voice, and takes the provider's defaults for everything else. The key
    comes from the account's vault at dial time, so a backup can only be a
    provider this account can actually authenticate to.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = ""
    voice: str = ""
    language: str = ""


class AmbientNoiseConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    volume: float = 0.3


class BackchannelConfigurationDefaults(BaseModel):
    """A filler ("hmm", "one moment") when the reply is slow to start.

    Off by default: the phrases have to be in the agent's language, and a
    filler in the wrong one is worse than the silence. ``delay_secs`` is how
    long the caller waits before hearing one; ``phrases`` are rotated. See
    services/pipecat/backchannel.py.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    delay_secs: float = 1.2
    phrases: list[str] = Field(default_factory=list)


class RecordingConfigurationDefaults(BaseModel):
    """Whether the call's audio is kept.

    On by default: the recording is what a call review, a QA grade and a
    dispute are settled with. Off means no audio is buffered or uploaded for
    the call — the transcript, the usage and the outcome are still kept, and
    the agent stops telling the caller the call is recorded, because it isn't.
    For the clinic or the lender whose compliance team says "no voice data at
    rest".
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class NoiseSuppressionConfigurationDefaults(BaseModel):
    """Noise off the caller's audio before the agent hears it.

    On by default: the people who call an Indian business are on a road, in a
    shop, on a bus. ``level`` is the share of the denoised signal in the blend
    the agent hears, 20 to 100 — see services/pipecat/noise_suppression.py.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    level: int = Field(default=100, ge=20, le=100)


class CallOutcome(BaseModel):
    """One label this agent's calls can be classified as afterwards.

    Distinct from ``call_disposition_codes`` on the workflow, which is not
    configuration at all: that column is a registry the pipeline appends to
    with every ``mapped_call_disposition`` it has actually seen, so the calls
    list can offer a dropdown of codes that occurred. This is the taxonomy a
    business *decides on* — the outcomes it wants sorted by, whether or not a
    call has produced one yet.

    Per workflow, because the outcomes are. A clinic books appointments, a
    lending agent gets a payment promise, an NDR agent confirms an address.
    Both Vapi and Bolna let you define the shape here rather than shipping a
    fixed set, and for the same reason: there isn't one.
    """

    model_config = ConfigDict(extra="allow")

    #: Narrower than the label, because it ends up a CRM field name, a URL
    #: parameter and a CSV column heading.
    code: str = Field(default="", max_length=40)
    label: str = Field(default="", max_length=60)
    #: What the model is told this code means. A classifier shown only a list
    #: of names invents its own definitions for them.
    when: str = Field(default="", max_length=200)


class OutcomeAction(BaseModel):
    """Something the agent does once the call is over.

    The other half of :class:`CallOutcome`. That one names what a call can turn
    out to be; this one says what should happen when it turns out that way --
    append the booking to the clinic's sheet, raise the CRM record, send the
    payment link.

    **After the call, not during, and that is the point.** The same action is
    already possible as a tool the agent calls mid-conversation, and for most
    of what a business wants it is the wrong shape: writing a row takes a
    second or more of a live line, and a caller listening to silence while we
    talk to Google is a worse experience than one whose booking is filed
    thirty seconds after they hang up. Nothing here is on the caller's clock,
    so nothing here needs a filler phrase, a timeout budget, or a decision from
    the model about whether it is worth the wait.

    Deterministic, too. A tool the agent *may* call is a tool it sometimes does
    not, and "always log the booking" cannot be built out of a model's
    judgement. This fires on the outcome code, or on every call.
    """

    model_config = ConfigDict(extra="allow")

    #: The tool to run, by uuid. Any tool the account already has -- the
    #: Composio connector, the Google Calendar integration, their own HTTP
    #: endpoint -- because an action worth taking after a call is the same
    #: action that was worth taking during one.
    tool_uuid: str = Field(min_length=1, max_length=64)

    #: Which outcomes this fires on, by ``CallOutcome.code``. Empty means every
    #: call, which is right for "log it" and wrong for "send the invoice" --
    #: the default is the broad one because a missing row is easier to notice
    #: than an invoice that went to somebody who did not buy anything.
    when: list[str] = Field(default_factory=list, max_length=30)

    #: Arguments for the tool, as templates over the call's own context --
    #: ``{{ gathered_context.customer_name }}``. Rendered once, after the call,
    #: against the same context the webhooks already use.
    arguments: dict[str, str] = Field(default_factory=dict)

    #: Off by default so adding one to a live agent is two deliberate acts
    #: rather than one.
    enabled: bool = True


class AgentScheduleSlot(BaseModel):
    """One window in an agent's week.

    Shaped like the campaign scheduler's slot on purpose: an operator who has
    set calling windows on a campaign should not meet a second, differently
    shaped idea of a week on the agent.
    """

    #: Monday is 0, matching datetime.weekday() and the campaign scheduler.
    #: Sunday-is-0 would shift every window by a day and read as a broken clock.
    day_of_week: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^([01]\d|2[0-4]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-4]):[0-5]\d$")


class AgentSchedule(BaseModel):
    """The hours an agent keeps, as something the platform can enforce.

    Narayani's opening hours live in its prompt -- "9:30 to 1:00 is when the
    clinic is open" -- so the agent can *say* them and the platform cannot
    *keep* them. A call at eleven at night is answered, a slot is agreed, and
    nobody at the clinic will honour it.

    Off by default, and off means always open: an agent nobody has scheduled
    behaves exactly as it does today. Everything ambiguous resolves to open as
    well -- see services/workflow/agent_hours.py -- because taking a number off
    the air is a worse failure than answering a call out of hours.
    """

    enabled: bool = False
    #: IST rather than UTC: every account on this platform is Indian, and a
    #: schedule that silently means something four and a half hours away is the
    #: kind of default nobody checks until a caller is turned away at nine.
    timezone: str = "Asia/Kolkata"
    slots: list[AgentScheduleSlot] = Field(default_factory=list, max_length=50)


class WorkflowConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _treat_null_as_unset(cls, data):
        # Stored configs (and older clients) carry explicit JSON nulls for
        # keys the user never configured; dropping them lets the field
        # defaults apply instead of failing validation.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    ambient_noise_configuration: AmbientNoiseConfigurationDefaults = Field(
        default_factory=AmbientNoiseConfigurationDefaults
    )
    noise_suppression_configuration: NoiseSuppressionConfigurationDefaults = Field(
        default_factory=NoiseSuppressionConfigurationDefaults
    )
    recording_configuration: RecordingConfigurationDefaults = Field(
        default_factory=RecordingConfigurationDefaults
    )
    backchannel_configuration: BackchannelConfigurationDefaults = Field(
        default_factory=BackchannelConfigurationDefaults
    )
    # Hang up when the caller says one of these ("okay bye", "that's all"),
    # after ``end_call_farewell`` if set, with no model turn in between.
    # Empty means off. See services/pipecat/end_call_phrases.py.
    end_call_phrases: list[str] = Field(default_factory=list)
    end_call_farewell: Optional[str] = None
    # May the agent hang up by itself? The phrase list above only reacts to the
    # caller saying goodbye, which cannot help when nobody is there to say it:
    # a tester said "I am not there", the agent replied "I will close the call",
    # and then talked for another twenty-four seconds, because saying so was
    # the only thing it could do. This gives it a tool that actually ends the
    # call. Off by default -- an agent that can hang up sometimes will when it
    # should not have. See services/pipecat/agent_end_call.py.
    agent_can_end_call: bool = False
    max_call_duration: int = Field(
        default=DEFAULT_MAX_CALL_DURATION_SECONDS,
        gt=0,
        le=MAX_CALL_DURATION_SECONDS,
    )
    max_user_idle_timeout: float = DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS
    smart_turn_stop_secs: float = DEFAULT_SMART_TURN_STOP_SECS
    # "default" resolves per transcriber: an STT that emits its own turn
    # boundaries owns interruption, otherwise a minimum word count does. "vad"
    # is that older raw-voice-activity fallback, kept selectable for a
    # transcriber that emits no interim results -- see
    # _create_non_realtime_user_turn_start_strategies.
    turn_start_strategy: Literal["default", "min_words", "provisional_vad", "vad"] = (
        DEFAULT_TURN_START_STRATEGY
    )
    turn_start_min_words: int = DEFAULT_TURN_START_MIN_WORDS
    provisional_vad_pause_secs: float = DEFAULT_PROVISIONAL_VAD_PAUSE_SECS
    turn_stop_strategy: Literal["transcription", "turn_analyzer"] = (
        DEFAULT_TURN_STOP_STRATEGY
    )
    # Only applies to the "transcription" strategy. A turn analyzer decides for
    # itself, and an STT that emits its own turn boundaries never waits at all.
    user_speech_timeout: float = Field(
        default=DEFAULT_USER_SPEECH_TIMEOUT, ge=0.15, le=3.0
    )
    # Where the caller is, in plain words, because an operator knows that and
    # does not know what a voice-activity confidence threshold is. "noisy"
    # raises the bar before a sound counts as the caller starting to speak, so
    # the next table does not interrupt the agent -- see vad_sensitivity for
    # what it can and cannot fix.
    caller_environment: Literal["quiet", "normal", "noisy"] = "normal"
    dictionary: str = ""
    # The hours this agent keeps. Off by default; off means always open.
    agent_schedule: AgentSchedule = Field(default_factory=AgentSchedule)
    interruption_backoff_secs: float = Field(
        default=DEFAULT_INTERRUPTION_BACKOFF_SECS, ge=0.0, le=3.0
    )
    context_compaction_enabled: bool = DEFAULT_CONTEXT_COMPACTION_ENABLED
    # Ordered backups, tried in turn when the service in front of them reports
    # a non-fatal error mid-call. Empty means what it always did: one provider,
    # and a failure the caller hears as dead air.
    #
    # Capped because each backup is a live connection held open for a failure
    # that usually never comes; past a couple the cost is certain and the
    # benefit is not.
    fallback_tts: list[FallbackServiceConfiguration] = Field(
        default_factory=list, max_length=2
    )
    fallback_stt: list[FallbackServiceConfiguration] = Field(
        default_factory=list, max_length=2
    )
    # What a finished call is classified as. Empty means the default list in
    # `services/workflow/disposition.py` applies, so an agent nobody configured
    # still produces outcomes rather than an empty column somebody has to
    # discover is empty.
    #
    # Capped because every entry is sent with every transcript, and past thirty
    # this is not a taxonomy anybody sorts by.
    call_outcomes: list[CallOutcome] = Field(default_factory=list, max_length=30)
    #: What happens once a call is over. Part of the versioned behavioural
    #: snapshot like everything else here, so "we added the Sheets step on the
    #: 14th" is answerable from the same place as "we changed the prompt".
    outcome_actions: list[OutcomeAction] = Field(default_factory=list, max_length=20)
    # Does the agent move with a caller who switches language mid-call?
    #
    # This replaced an environment variable, which made following a property of
    # the deployment rather than of the agent: a clinic line wants it and the
    # same account's compliance line, reading a disclosure approved in one
    # language, does not.
    #
    # Off by default. Nothing follows today, so defaulting it on would change
    # the behaviour of every live call in one deploy on a judgement nobody made
    # per agent.
    follow_caller_language: bool = False
    # Does the agent read the caller's keypad?
    #
    # Off by default for the same reason as above — this adds a turn the agent
    # did not previously get, and an agent whose prompt has no idea what to do
    # with "[the caller pressed 1]" answers it badly. Turned on where the
    # prompt asks for a number, which is where it is worth having: speech
    # recognition is at its worst on a ten-digit mobile number and the keypad
    # is at its best.
    accept_keypad_input: bool = False
    # Speak the way callers here actually do, mixing English into the local
    # language, rather than in the formal register a model reaches for when
    # told to speak Hindi.
    #
    # Off by default because the instruction is appended to the operator's own
    # prompt, and somebody who wrote "reply only in formal Hindi" meant it.
    # On: Tamil words in Tamil, English words in English, the way people
    # talk on the phone. An operator who wants formal, single-language
    # speech switches it off.
    speak_like_callers: bool = True
    # Which languages this agent may answer in at all, as BCP-47 tags or bare
    # subtags ("ta", "ta-IN", "en"). Empty means no restriction, which is how
    # every agent behaved before this existed.
    #
    # It exists because speech recognition guesses the language of every
    # utterance and gets short ones wrong. That was survivable while a wrong
    # guess only changed the voice; once the model is told too, one bad guess
    # answers a Tamil caller in Telugu. Naming the two or three languages a
    # line actually serves turns a wrong guess back into noise.
    agent_languages: list[str] = Field(default_factory=list)


def get_default_workflow_configurations() -> WorkflowConfigurationDefaults:
    return WorkflowConfigurationDefaults()


def new_agent_workflow_configurations() -> dict:
    """What an agent created today starts with, over and above the field defaults.

    Two different questions wear the same word "default" here, and conflating
    them is what kept this feature off. A field default is what an agent with
    nothing stored behaves as, and it governs every agent already running: it
    has to stay conservative, because changing it changes live calls in one
    deploy on a judgement nobody made per agent. This is the other question —
    what a brand-new agent is set up as — and it is free to be opinionated,
    because there is no existing behaviour to change.

    ``follow_caller_language`` is the case that made the distinction worth
    drawing. Its field default is off, and the reason given was that nothing
    followed anyway: the switch changed the voice and left the model answering
    in whatever language its prompt was written in. That is fixed, so the
    reason is gone — but the field default still cannot move, because an agent
    running today with nothing stored would start behaving differently on the
    next call. A new agent has no such call to disturb, and for an India-first
    product it should ship able to follow the caller. Written explicitly rather
    than inherited, so an operator turning it off keeps it off.
    """
    return {"follow_caller_language": True}
