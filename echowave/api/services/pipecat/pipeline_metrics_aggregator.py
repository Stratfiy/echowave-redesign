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
        # "{processor}|||{model}" of the STT service on this pipeline, when
        # there is one. Set by run_pipeline; see register_stt_service.
        self._stt_key: Optional[str] = None

        # Per-turn latency instrumentation. TTFB arrives continuously as
        # MetricsFrames; the turn is only *closed* when the latency observer
        # reports a measured user-to-bot latency, so these accumulate against
        # the turn in progress and are flushed by record_turn_latency.
        self._turn_stage_ttfb: Dict[str, float] = {}
        self._turns: list[dict] = []

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
        between the caller finishing and audio going back out.

        ``call_turn_metrics`` stores a cumulative timeline in milliseconds from
        the moment the user stopped, and derives each stage as the difference
        between consecutive marks, so the per-stage TTFBs banked during the turn
        are laid end to end here.

        Endpointing is not separately instrumented — pipecat reports no VAD
        mark — so its window is zero-length and the stage reads as 0 rather
        than being invented. Playback falls out as the honest residual between
        the last mark and audio going out.
        """
        stages = self._turn_stage_ttfb
        self._turn_stage_ttfb = {}

        def ms(seconds: float) -> int:
            return max(int(round(seconds * 1000)), 0)

        latency_ms = ms(latency_seconds)

        t_user_stopped = 0
        t_endpoint_fired = 0  # not separately measured; see docstring
        t_stt_final = t_endpoint_fired + ms(stages.get("stt_ms", 0.0))
        t_llm_first_token = t_stt_final + ms(stages.get("llm_ms", 0.0))
        t_tts_first_byte = t_llm_first_token + ms(stages.get("tts_ms", 0.0))
        # A stage chain longer than the measured latency would make playback
        # negative; clamp so the residual is never nonsense.
        t_audio_out = max(latency_ms, t_tts_first_byte)

        self._turns.append(
            {
                "turn_index": len(self._turns),
                "latency_ms": latency_ms,
                "t_user_stopped_ms": t_user_stopped,
                "t_endpoint_fired_ms": t_endpoint_fired,
                "t_stt_final_ms": t_stt_final,
                "t_llm_first_token_ms": t_llm_first_token,
                "t_tts_first_byte_ms": t_tts_first_byte,
                "t_audio_out_ms": t_audio_out,
            }
        )

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
            "call_duration_seconds": call_duration,
        }

    def reset_metrics(self):
        """Reset all aggregated metrics."""
        self._llm_usage_metrics.clear()
        self._tts_usage_metrics.clear()
        self._stt_usage_metrics.clear()
        self._turn_stage_ttfb.clear()
        self._turns.clear()
        self._start_time = None
        self._stop_time = None
