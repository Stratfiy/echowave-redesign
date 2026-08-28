import time
from collections import defaultdict
from typing import Dict, Optional

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    MetricsFrame,
    StartFrame,
)
from pipecat.metrics.metrics import (
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def _ms(seconds: float) -> int:
    """Seconds to whole milliseconds, never negative."""
    return max(int(round(seconds * 1000)), 0)


def _stage_end_ms(ttfb: list, stage: str, origin: float) -> Optional[int]:
    """When ``stage`` produced its first byte, in ms after ``origin``.

    Stage identity comes from the processor's class name, the same convention
    the usage keys use. A processor that matches nothing is skipped rather than
    guessed at: a wrong attribution on a diagnostic chart is worse than a gap,
    because a gap is visible and a wrong number is not.

    The earliest match wins where a pipeline has more than one processor of a
    kind — the first byte of the turn is what the caller waits on.
    """
    ends = [
        entry.start_time + entry.duration_secs
        for entry in ttfb
        if f"{stage}service" in (entry.processor or "").lower()
    ]
    return _ms(min(ends) - origin) if ends else None


class PipelineMetricsAggregator(FrameProcessor):
    def __init__(self):
        super().__init__()
        # Structure: {f"{processor}|||{model}": aggregated_metrics}
        # For LLM: aggregated_metrics is LLMTokenUsage
        # For TTS: aggregated_metrics is int (total characters)
        # For STT: aggregated_metrics is float (total seconds)

        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None
        self._llm_usage_metrics: Dict[str, LLMTokenUsage] = {}
        self._tts_usage_metrics: Dict[str, int] = defaultdict(int)
        self._stt_usage_metrics: Dict[str, float] = defaultdict(float)
        # {f"{provider}|||{model}": total tokens}. Unlike the three above, this
        # is never populated by a pipecat frame — an embedding call during
        # in-call knowledge-base retrieval happens outside the frame pipeline
        # entirely (a direct HTTP call from a tool function), so it is
        # recorded via register_embedding_usage instead. See
        # PipecatEngine._record_embedding_usage.
        self._embedding_usage_metrics: Dict[str, int] = defaultdict(int)
        # "{processor}|||{model}" of the STT service on this pipeline, when
        # there is one. Set by run_pipeline; see register_stt_service.
        self._stt_key: Optional[str] = None

        # {"llm"|"stt"|"tts": "byok"|"managed"}, one entry per component this
        # pipeline actually used. Set by run_pipeline/text_chat_runner from the
        # resolved configuration's key_source; see register_key_sources.
        self._key_sources: Dict[str, str] = {}

        # Priced features that actually ran on this call — see
        # services/billing/addons.py. Recorded on use rather than on
        # configuration: a knowledge base attached to a node the conversation
        # never reached has cost the customer nothing.
        self._addons_used: set = set()

        # Per-turn latency instrumentation. TTFB arrives continuously as
        # MetricsFrames; the turn is only *closed* when the latency observer
        # reports a measured user-to-bot latency, so these accumulate against
        # the turn in progress and are flushed by record_turn_latency.
        self._turn_stage_ttfb: Dict[str, float] = {}
        # Token usage banked against the turn in progress, on the same lifecycle
        # as the stage TTFBs above. The call-wide totals in _llm_usage_metrics
        # give a number with no shape: ten short exchanges and three long ones
        # sum identically, and only the shape says whether context growth is
        # what is costing the money.
        self._turn_tokens: Dict[str, int] = {}
        self._turns: list[dict] = []
        # Set when a turn closes, cleared when its breakdown amends it. Guards
        # against the greeting's breakdown, which has no turn behind it.
        self._turn_awaiting_breakdown: bool = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self._start(frame)
        elif isinstance(frame, EndFrame):
            await self._stop(frame)
        elif isinstance(frame, CancelFrame):
            await self._cancel(frame)
        elif isinstance(frame, MetricsFrame):
            for data in frame.data:
                if isinstance(data, LLMUsageMetricsData):
                    await self._handle_llm_usage_metrics(data)
                elif isinstance(data, TTSUsageMetricsData):
                    await self._handle_tts_usage_metrics(data)
                elif isinstance(data, TTFBMetricsData):
                    self._handle_ttfb_metrics(data)

        await self.push_frame(frame, direction)

    async def _start(self, _: StartFrame):
        """Start tracking call duration."""
        self._start_time = time.time()
        self._stop_time = None

    async def _stop(self, _: EndFrame):
        """Stop tracking call duration."""
        if self._start_time is not None and self._stop_time is None:
            self._stop_time = time.time()

    async def _cancel(self, _: CancelFrame):
        """Handle call cancellation - also stop tracking duration."""
        if self._start_time is not None and self._stop_time is None:
            self._stop_time = time.time()

    async def _handle_llm_usage_metrics(self, data: LLMUsageMetricsData):
        key = f"{data.processor}|||{data.model}"
        new_usage = data.value

        if key in self._llm_usage_metrics:
            # Aggregate with existing metrics
            existing = self._llm_usage_metrics[key]
            aggregated = LLMTokenUsage(
                prompt_tokens=existing.prompt_tokens + new_usage.prompt_tokens,
                completion_tokens=existing.completion_tokens
                + new_usage.completion_tokens,
                total_tokens=existing.total_tokens + new_usage.total_tokens,
                cache_read_input_tokens=(existing.cache_read_input_tokens or 0)
                + (new_usage.cache_read_input_tokens or 0),
                cache_creation_input_tokens=(existing.cache_creation_input_tokens or 0)
                + (new_usage.cache_creation_input_tokens or 0),
            )
            self._llm_usage_metrics[key] = aggregated
        else:
            # First occurrence for this processor+model combination
            self._llm_usage_metrics[key] = LLMTokenUsage(
                prompt_tokens=new_usage.prompt_tokens,
                completion_tokens=new_usage.completion_tokens,
                total_tokens=new_usage.total_tokens,
                cache_read_input_tokens=new_usage.cache_read_input_tokens,
                cache_creation_input_tokens=new_usage.cache_creation_input_tokens,
            )

        # Same numbers, banked against the open turn as well as the call. An
        # LLM can be invoked more than once in a turn (a tool round-trip), so
        # these add rather than replace.
        self._turn_tokens["prompt_tokens"] = self._turn_tokens.get(
            "prompt_tokens", 0
        ) + (new_usage.prompt_tokens or 0)
        self._turn_tokens["completion_tokens"] = self._turn_tokens.get(
            "completion_tokens", 0
        ) + (new_usage.completion_tokens or 0)
        self._turn_tokens["cached_tokens"] = self._turn_tokens.get(
            "cached_tokens", 0
        ) + (new_usage.cache_read_input_tokens or 0)

        logger.debug(f"LLM usage metrics: {self._llm_usage_metrics}")

    async def _handle_tts_usage_metrics(self, data: TTSUsageMetricsData):
        key = f"{data.processor}|||{data.model}"
        self._tts_usage_metrics[key] += data.value
        # logger.debug(f"TTS usage metrics: {self._tts_usage_metrics}")

    def _handle_ttfb_metrics(self, data: TTFBMetricsData) -> None:
        """Bank a stage's time-to-first-byte against the turn in progress.

        Which stage a processor is comes from its class name — the same
        convention the usage keys rely on. An unrecognised processor is
        ignored rather than guessed at, leaving that stage NULL, because a
        wrong attribution is worse on a diagnostic chart than a gap.
        """
        name = (data.processor or "").lower()
        if "sttservice" in name:
            stage = "stt_ms"
        elif "llmservice" in name:
            stage = "llm_ms"
        elif "ttsservice" in name:
            stage = "tts_ms"
        else:
            return
        # First byte of the turn is what counts; a later frame from the same
        # stage belongs to the next response, not this one.
        self._turn_stage_ttfb.setdefault(stage, data.value)

    def record_turn_latency(self, latency_seconds: float) -> None:
        """Close the current turn with its measured user-to-bot latency.

        Called from the pipeline's ``on_latency_measured`` handler. This is the
        perceived latency the dashboard reports percentiles over — the gap
        between the caller actually falling silent and audio going back out.
        (The observer subtracts the VAD's ``stop_secs`` from its determination
        time, so the zero point is the real end of speech, not the moment the
        VAD noticed.)

        ``call_turn_metrics`` stores a cumulative timeline in milliseconds from
        that zero point. The marks laid down here are provisional: they chain
        the per-stage TTFBs end to end, which assumes the stages ran strictly
        back to back with no gap. ``record_turn_breakdown`` replaces them with
        real timeline positions when the breakdown for this turn arrives a
        moment later — see there for why the difference matters.
        """
        stages = self._turn_stage_ttfb
        self._turn_stage_ttfb = {}
        tokens = self._turn_tokens
        self._turn_tokens = {}

        latency_ms = _ms(latency_seconds)

        t_stt_final = _ms(stages.get("stt_ms", 0.0))
        t_llm_first_token = t_stt_final + _ms(stages.get("llm_ms", 0.0))
        t_tts_first_byte = t_llm_first_token + _ms(stages.get("tts_ms", 0.0))

        self._turns.append(
            {
                "turn_index": len(self._turns),
                "latency_ms": latency_ms,
                "t_user_stopped_ms": 0,
                # Left NULL until the breakdown supplies a measured value. A
                # zero here would be read as "endpointing is free", which is
                # both false and the opposite of actionable — it is usually the
                # largest stage in the turn.
                "t_endpoint_fired_ms": None,
                "t_stt_final_ms": t_stt_final,
                "t_llm_first_token_ms": t_llm_first_token,
                "t_tts_first_byte_ms": t_tts_first_byte,
                "t_audio_out_ms": max(latency_ms, t_tts_first_byte),
                # None rather than 0 when the provider reported no usage for
                # this turn. A zero would drag the median context size down and
                # read as an agent that got cheaper.
                "prompt_tokens": tokens.get("prompt_tokens") or None,
                "completion_tokens": tokens.get("completion_tokens") or None,
                "cached_tokens": tokens.get("cached_tokens") or None,
            }
        )
        self._turn_awaiting_breakdown = True

    def record_turn_breakdown(self, breakdown) -> None:
        """Replace the last turn's provisional marks with measured ones.

        Called from ``on_latency_breakdown``, which the observer emits straight
        after ``on_latency_measured`` for the same cycle — so this amends the
        turn just closed rather than opening a new one.

        Two things only the breakdown knows:

        **When the turn was released.** ``user_turn_secs`` runs from the user
        actually falling silent to the turn being handed to the LLM, covering
        VAD silence detection, transcript finalisation and any turn-analyzer
        wait. Nothing else in the pipeline reports it, and on a typical call it
        is the single largest stage — an 800ms silence threshold is 800ms the
        caller sits through before anything downstream has started. Without it
        that time does not vanish from the total; it lands in whichever bucket
        happens to be computed as the remainder, which is how a team ends up
        optimising a fast stage while the slow one is invisible.

        **Where each stage actually sat.** Each TTFB carries the wall-clock
        instant it began, so the marks are positions on a real timeline rather
        than durations chained end to end. Chaining silently assumes no gaps
        between stages, and any gap it misses is absorbed by the residual.

        A breakdown also arrives for the opening greeting, which has no user
        turn behind it and so closed no turn — that one is ignored.
        """
        if not getattr(self, "_turn_awaiting_breakdown", False) or not self._turns:
            return
        self._turn_awaiting_breakdown = False

        turn = self._turns[-1]
        latency_ms = turn["latency_ms"]

        user_turn_secs = getattr(breakdown, "user_turn_secs", None)
        if user_turn_secs is not None:
            # Clamped to the turn: a release mark past the moment audio went
            # out would make every later stage negative. That it fired at all
            # is a measurement fault worth seeing rather than propagating.
            turn["t_endpoint_fired_ms"] = min(_ms(user_turn_secs), latency_ms)

        origin = getattr(breakdown, "user_turn_start_time", None)
        ttfb = list(getattr(breakdown, "ttfb", None) or [])
        if origin is not None and ttfb:
            for stage, column in (
                ("stt", "t_stt_final_ms"),
                ("llm", "t_llm_first_token_ms"),
                ("tts", "t_tts_first_byte_ms"),
            ):
                mark = _stage_end_ms(ttfb, stage, origin)
                if mark is not None:
                    turn[column] = min(mark, latency_ms)

        # Marks must not run backwards. A stage whose measured end precedes the
        # one before it is either an overlap the flat timeline cannot express
        # or a clock artefact; either way the later mark wins, so a stage can
        # read as instant but never as negative.
        floor = turn["t_endpoint_fired_ms"] or 0
        for column in ("t_stt_final_ms", "t_llm_first_token_ms", "t_tts_first_byte_ms"):
            if turn[column] is None:
                continue
            turn[column] = max(turn[column], floor)
            floor = turn[column]

        turn["t_audio_out_ms"] = max(latency_ms, floor)

    def get_turn_metrics(self) -> list[dict]:
        """Per-turn rows for ``call_turn_metrics``, in turn order."""
        return list(self._turns)

    def register_stt_service(self, stt) -> None:
        """Record which STT service this pipeline is using, for billing.

        Pipecat emits no STT usage metric — there is no ``STTUsageMetricsData``
        the way there is for LLM and TTS — so nothing ever populated the STT
        bucket and speech-to-text fell out of provider cost entirely.

        Streaming STT providers bill on audio streamed, and on a live call the
        caller's audio streams for the call's whole duration, so the billable
        quantity is the call duration. What we cannot infer is *which* service
        was used, so the pipeline tells us here.

        Passing None (a realtime speech-to-speech pipeline, which has no
        separate STT stage) leaves the bucket empty, which is correct: there is
        no separate STT charge on those calls.
        """
        if stt is None:
            self._stt_key = None
            return
        model = getattr(getattr(stt, "_settings", None), "model", "") or ""
        self._stt_key = f"{stt.name}|||{model if isinstance(model, str) else ''}"

    def register_key_sources(self, key_sources: Dict[str, Optional[str]]) -> None:
        """Record which components on this call ran on the account's own key.

        Read back off ``usage_info`` by billing at cost time to decide whether
        a component's usage was ours to charge for. A missing or unrecognised
        entry is simply not stored -- billing treats an absent key means
        "managed" (charge as before), never "byok" (which would silently stop
        charging for usage no one confirmed was BYOK).
        """
        for component, source in (key_sources or {}).items():
            if source in ("byok", "managed"):
                self._key_sources[component] = source

    def register_embedding_usage(
        self, *, provider: str, model: str, tokens: int
    ) -> None:
        """Record real vendor usage from a query-time embedding call.

        Additive across repeated retrieval calls in the same conversation —
        a call that searches the knowledge base three times pays for three
        embeddings, and the key (provider + model) is exactly what
        ``billing/usage.py`` needs to resolve a rate against, the same shape
        every other component here already uses.
        """
        if tokens <= 0:
            return
        key = f"{provider}|||{model}"
        self._embedding_usage_metrics[key] += tokens

    def register_addon_used(self, addon_key: str) -> None:
        """Record that a priced feature ran on this call.

        Idempotent, and called from the feature's own execution path rather
        than from the code that enabled it, so what reaches the invoice is what
        the caller actually received.
        """
        if addon_key:
            self._addons_used.add(addon_key)

    def get_llm_usage_metrics(self) -> Dict[str, LLMTokenUsage]:
        """Get the aggregated LLM usage metrics grouped by processor|||model."""
        return self._llm_usage_metrics

    def get_tts_usage_metrics(self) -> Dict[str, int]:
        """Get the aggregated TTS usage metrics grouped by processor|||model."""
        return self._tts_usage_metrics

    def get_stt_usage_metrics(self) -> Dict[str, float]:
        """Get the aggregated STT usage metrics grouped by processor|||model."""
        return self._stt_usage_metrics

    def get_call_duration(self) -> float:
        """Get call duration"""
        if self._start_time is None:
            return 0.0

        if self._stop_time is None:
            call_duration = time.time() - self._start_time
        else:
            call_duration = self._stop_time - self._start_time

        # Lets return a rounded integer
        return int(round(call_duration))

    def get_all_usage_metrics_serialized(self) -> Dict[str, Dict[str, any]]:
        """Get all aggregated usage metrics in JSON-serializable format."""
        serialized_llm = {}
        for key, usage in self._llm_usage_metrics.items():
            serialized_llm[key] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            }

        call_duration = self.get_call_duration()

        # Streaming STT bills on the audio streamed to it, which for a live
        # call is the call itself. Reported here rather than accumulated
        # frame-by-frame because pipecat emits no STT usage metric.
        stt = dict(self._stt_usage_metrics)
        if self._stt_key and call_duration > 0:
            stt.setdefault(self._stt_key, call_duration)

        return {
            "llm": serialized_llm,
            "tts": dict(self._tts_usage_metrics),
            "stt": stt,
            "embedding": dict(self._embedding_usage_metrics),
            "call_duration_seconds": call_duration,
            "key_sources": dict(self._key_sources),
            # Sorted so two calls that used the same features serialize
            # identically; a set's iteration order would make otherwise equal
            # receipts differ.
            "addons": sorted(self._addons_used),
        }

    def reset_metrics(self):
        """Reset all aggregated metrics."""
        self._llm_usage_metrics.clear()
        self._tts_usage_metrics.clear()
        self._stt_usage_metrics.clear()
        self._embedding_usage_metrics.clear()
        self._key_sources.clear()
        self._addons_used.clear()
        self._turn_stage_ttfb.clear()
        self._turns.clear()
        self._start_time = None
        self._stop_time = None
