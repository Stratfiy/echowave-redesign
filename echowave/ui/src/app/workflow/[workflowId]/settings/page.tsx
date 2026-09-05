"use client";

import { format } from "date-fns";
import { ArrowLeft, BookA, Brain, CalendarIcon, ChevronRight, Clipboard, Download, ExternalLink, FileDown, Fingerprint, Loader2, Mic, Pause, PhoneOff, Play, Rocket, Settings, Trash2Icon, Upload, Variable, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import {
    downloadWorkflowReportApiV1WorkflowWorkflowIdReportGet,
    getAmbientNoiseUploadUrlApiV1WorkflowAmbientNoiseUploadUrlPost,
    getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    getWorkflowApiV1WorkflowFetchWorkflowIdGet,
} from "@/client/sdk.gen";
import type {
    OrganizationAiModelConfigurationResponse,
    OrganizationAiModelConfigurationV2,
    WorkflowResponse,
} from "@/client/types.gen";
import {
    AIModelConfigurationV2Editor,
    type ModelConfigurationDefaultsV2,
} from "@/components/AIModelConfigurationV2Editor";
import { CostPerMinuteBar } from "@/components/CostPerMinuteBar";
import { FallbackChain } from "@/components/FallbackChain";
import { FlowEdge, FlowNode } from "@/components/flow/types";
import { LLMConfigSelector } from "@/components/LLMConfigSelector";
import SpinLoader from "@/components/SpinLoader";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";
import { UnsavedChangesProvider, useUnsavedChanges, useUnsavedChangesContext } from "@/context/UnsavedChangesContext";
import { useAudioPlayback } from "@/hooks/useAudioPlayback";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { priceableFromV2, pricedStack } from "@/lib/billing/pricedStack";
import logger from "@/lib/logger";
import { cn } from "@/lib/utils";
import {
    type AmbientNoiseConfiguration,
    DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
    DEFAULT_TURN_START_MIN_WORDS,
    DEFAULT_USER_SPEECH_TIMEOUT,
    DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
    type FallbackService,
    LATENCY_PRESETS,
    type LatencyPreset,
    matchLatencyPreset,
    MAX_USER_SPEECH_TIMEOUT,
    MIN_USER_SPEECH_TIMEOUT,
    resolveWorkflowConfigurations,
    TURN_START_STRATEGY_OPTIONS,
    type TurnStartStrategy,
    type TurnStopStrategy,
    type VoicemailDetectionConfiguration,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

import { AgentTabs } from "../components/AgentTabs";
import { QaCard } from "../components/QaCard";
import { useWorkflowState } from "../hooks/useWorkflowState";
import { DEFAULT_TAB, isTabId, type TabId, TABS } from "./tabs";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_VOICEMAIL_SYSTEM_PROMPT = `You are a voicemail detection classifier for an OUTBOUND calling system. A bot has called a phone number and you need to determine if a human answered or if the call went to voicemail based on the provided text.

HUMAN ANSWERED - LIVE CONVERSATION (respond "CONVERSATION"):
- Personal greetings: "Hello?", "Hi", "Yeah?", "John speaking"
- Interactive responses: "Who is this?", "What do you want?", "Can I help you?"
- Conversational tone expecting back-and-forth dialogue
- Questions directed at the caller: "Hello? Anyone there?"
- Informal responses: "Yep", "What's up?", "Speaking"
- Natural, spontaneous speech patterns
- Immediate acknowledgment of the call

VOICEMAIL SYSTEM (respond "VOICEMAIL"):
- Automated voicemail greetings: "Hi, you've reached [name], please leave a message"
- Phone carrier messages: "The number you have dialed is not in service", "Please leave a message", "All circuits are busy"
- Professional voicemail: "This is [name], I'm not available right now"
- Instructions about leaving messages: "leave a message", "leave your name and number"
- References to callback or messaging: "call me back", "I'll get back to you"
- Carrier system messages: "mailbox is full", "has not been set up"
- Business hours messages: "our office is currently closed"

Respond with ONLY "CONVERSATION" if a person answered, or "VOICEMAIL" if it's voicemail/recording.`;

// Sidebar navigation items


// ---------------------------------------------------------------------------
// Section: Report
// ---------------------------------------------------------------------------

function ReportSection({ workflowId }: { workflowId: number }) {
    const [startDate, setStartDate] = useState<Date | undefined>(undefined);
    const [startTime, setStartTime] = useState("00:00");
    const [endDate, setEndDate] = useState<Date | undefined>(undefined);
    const [endTime, setEndTime] = useState("23:59");
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);
    const [isDownloading, setIsDownloading] = useState(false);

    const buildDateTime = (date: Date | undefined, time: string): string | undefined => {
        if (!date) return undefined;
        const [hours, minutes] = time.split(":").map(Number);
        const combined = new Date(date);
        combined.setHours(hours, minutes, 0, 0);
        return combined.toISOString();
    };

    const handleDownload = async () => {
        setIsDownloading(true);
        setIsPopoverOpen(false);
        try {
            const response = await downloadWorkflowReportApiV1WorkflowWorkflowIdReportGet({
                path: { workflow_id: workflowId },
                query: {
                    start_date: buildDateTime(startDate, startTime),
                    end_date: buildDateTime(endDate, endTime),
                },
                parseAs: "blob",
            });

            if (response.data) {
                const blob = response.data as Blob;
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = url;
                a.download = `workflow_${workflowId}_report.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
            } else {
                toast.error("Failed to download report");
            }
        } catch (err) {
            logger.error(`Failed to download workflow report: ${err}`);
            toast.error("Failed to download report");
        } finally {
            setIsDownloading(false);
        }
    };

    const handleClear = () => {
        setStartDate(undefined);
        setStartTime("00:00");
        setEndDate(undefined);
        setEndTime("23:59");
    };

    return (
        <Card id="report">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <FileDown className="h-4 w-4" />
                    Report
                </CardTitle>
                <CardDescription>
                    Download a CSV report of completed runs for this agent, optionally filtered by date range.
                </CardDescription>
            </CardHeader>
            <CardFooter className="border-t pt-6">
                <Popover open={isPopoverOpen} onOpenChange={setIsPopoverOpen}>
                    <PopoverTrigger asChild>
                        <Button variant="outline" disabled={isDownloading}>
                            <Download className="h-4 w-4 mr-2" />
                            Download Report
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-auto p-4" align="start">
                        <div className="space-y-4">
                            <div className="text-sm font-medium">Filter by date range</div>
                            <div className="grid gap-3">
                                <div className="space-y-1.5">
                                    <Label className="text-xs">From</Label>
                                    <div className="flex gap-2">
                                        <Popover>
                                            <PopoverTrigger asChild>
                                                <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                    <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                    {startDate ? format(startDate, "MMM dd, yyyy") : "Start date"}
                                                </Button>
                                            </PopoverTrigger>
                                            <PopoverContent className="w-auto p-0" align="start">
                                                <Calendar
                                                    mode="single"
                                                    selected={startDate}
                                                    onSelect={setStartDate}
                                                    disabled={(date) => (endDate ? date > endDate : false)}
                                                />
                                            </PopoverContent>
                                        </Popover>
                                        <Input
                                            type="time"
                                            value={startTime}
                                            onChange={(e) => setStartTime(e.target.value)}
                                            className="w-[100px] h-8 text-xs"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-1.5">
                                    <Label className="text-xs">To</Label>
                                    <div className="flex gap-2">
                                        <Popover>
                                            <PopoverTrigger asChild>
                                                <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                    <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                    {endDate ? format(endDate, "MMM dd, yyyy") : "End date"}
                                                </Button>
                                            </PopoverTrigger>
                                            <PopoverContent className="w-auto p-0" align="start">
                                                <Calendar
                                                    mode="single"
                                                    selected={endDate}
                                                    onSelect={setEndDate}
                                                    disabled={(date) => (startDate ? date < startDate : false)}
                                                />
                                            </PopoverContent>
                                        </Popover>
                                        <Input
                                            type="time"
                                            value={endTime}
                                            onChange={(e) => setEndTime(e.target.value)}
                                            className="w-[100px] h-8 text-xs"
                                        />
                                    </div>
                                </div>
                            </div>
                            <Separator />
                            <div className="flex justify-between">
                                <Button variant="ghost" size="sm" onClick={handleClear}>
                                    Clear
                                </Button>
                                <Button size="sm" onClick={handleDownload} disabled={isDownloading}>
                                    <Download className="h-3.5 w-3.5 mr-1.5" />
                                    {startDate || endDate ? "Download Filtered" : "Download All"}
                                </Button>
                            </div>
                        </div>
                    </PopoverContent>
                </Popover>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: General
// ---------------------------------------------------------------------------

const MAX_AMBIENT_NOISE_FILE_SIZE = 10 * 1024 * 1024; // 10MB

function GeneralSection({
    workflowConfigurations,
    workflowName,
    workflowId,
    onSave,
    modelConfigurationDefaults,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    workflowId: number;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
    modelConfigurationDefaults: ModelConfigurationDefaultsV2 | null;
}) {
    const [name, setName] = useState(workflowName);
    const [ambientNoiseConfig, setAmbientNoiseConfig] = useState<AmbientNoiseConfiguration>(
        workflowConfigurations.ambient_noise_configuration,
    );
    const [maxCallDuration, setMaxCallDuration] = useState(workflowConfigurations.max_call_duration);
    const [maxUserIdleTimeout, setMaxUserIdleTimeout] = useState(workflowConfigurations.max_user_idle_timeout);
    const [smartTurnStopSecs, setSmartTurnStopSecs] = useState(workflowConfigurations.smart_turn_stop_secs);
    const [turnStartStrategy, setTurnStartStrategy] = useState<TurnStartStrategy>(
        workflowConfigurations.turn_start_strategy,
    );
    const [turnStartMinWords, setTurnStartMinWords] = useState(
        workflowConfigurations.turn_start_min_words,
    );
    const [provisionalVadPauseSecs, setProvisionalVadPauseSecs] = useState(
        workflowConfigurations.provisional_vad_pause_secs,
    );
    const [turnStopStrategy, setTurnStopStrategy] = useState<TurnStopStrategy>(
        workflowConfigurations.turn_stop_strategy,
    );
    const [userSpeechTimeout, setUserSpeechTimeout] = useState(
        workflowConfigurations.user_speech_timeout,
    );
    const [contextCompactionEnabled, setContextCompactionEnabled] = useState(
        workflowConfigurations.context_compaction_enabled,
    );
    const [includeTranscriptEndTimestamps, setIncludeTranscriptEndTimestamps] = useState(
        workflowConfigurations.transcript_configuration?.include_end_timestamps ?? false,
    );
    const [interruptionBackoffSecs, setInterruptionBackoffSecs] = useState(
        workflowConfigurations.interruption_backoff_secs,
    );
    const [fallbackTts, setFallbackTts] = useState<FallbackService[]>(
        workflowConfigurations.fallback_tts ?? [],
    );
    const [fallbackStt, setFallbackStt] = useState<FallbackService[]>(
        workflowConfigurations.fallback_stt ?? [],
    );
    // Advanced starts open only when it already holds something non-default,
    // so nothing a person set is hidden behind a closed group. Both default to
    // false server-side: context compaction enabled, transcript end timestamps.
    const advancedTouched = contextCompactionEnabled || includeTranscriptEndTimestamps;

    const [isSaving, setIsSaving] = useState(false);
    const [isUploadingAudio, setIsUploadingAudio] = useState(false);

    // Derived, never stored: the preset is a reading of the three numbers, so
    // a workflow tuned by hand keeps its values and simply reads as Custom.
    const activePreset = matchLatencyPreset({
        user_speech_timeout: userSpeechTimeout,
        smart_turn_stop_secs: smartTurnStopSecs,
        turn_start_min_words: turnStartMinWords,
    });

    const applyLatencyPreset = (preset: LatencyPreset) => {
        const { user_speech_timeout, smart_turn_stop_secs, turn_start_min_words } =
            LATENCY_PRESETS[preset].values;
        setUserSpeechTimeout(user_speech_timeout);
        setSmartTurnStopSecs(smart_turn_stop_secs);
        setTurnStartMinWords(turn_start_min_words);
    };
    const [audioUploadError, setAudioUploadError] = useState<string | null>(null);
    const ambientFileInputRef = useRef<HTMLInputElement>(null);
    const { playingId, toggle: togglePlayback } = useAudioPlayback();
    const selectedTurnStartStrategy = TURN_START_STRATEGY_OPTIONS.find(
        (option) => option.value === turnStartStrategy,
    );

    const isDirty = useMemo(() => {
        const initAmbient = workflowConfigurations.ambient_noise_configuration;
        return (
            name !== workflowName ||
            JSON.stringify(ambientNoiseConfig) !== JSON.stringify(initAmbient) ||
            maxCallDuration !== workflowConfigurations.max_call_duration ||
            maxUserIdleTimeout !== workflowConfigurations.max_user_idle_timeout ||
            smartTurnStopSecs !== workflowConfigurations.smart_turn_stop_secs ||
            turnStartStrategy !== workflowConfigurations.turn_start_strategy ||
            turnStartMinWords !== workflowConfigurations.turn_start_min_words ||
            provisionalVadPauseSecs !== workflowConfigurations.provisional_vad_pause_secs ||
            turnStopStrategy !== workflowConfigurations.turn_stop_strategy ||
            userSpeechTimeout !== workflowConfigurations.user_speech_timeout ||
            interruptionBackoffSecs !== workflowConfigurations.interruption_backoff_secs ||
            JSON.stringify(fallbackTts) !== JSON.stringify(workflowConfigurations.fallback_tts ?? []) ||
            JSON.stringify(fallbackStt) !== JSON.stringify(workflowConfigurations.fallback_stt ?? []) ||
            contextCompactionEnabled !== workflowConfigurations.context_compaction_enabled ||
            includeTranscriptEndTimestamps !==
            (workflowConfigurations.transcript_configuration?.include_end_timestamps ?? false)
        );
    }, [name, workflowName, ambientNoiseConfig, maxCallDuration, maxUserIdleTimeout, smartTurnStopSecs, turnStartStrategy, turnStartMinWords, provisionalVadPauseSecs, turnStopStrategy, userSpeechTimeout, interruptionBackoffSecs, fallbackTts, fallbackStt, contextCompactionEnabled, includeTranscriptEndTimestamps, workflowConfigurations]);

    useUnsavedChanges("general", isDirty);

    const handleAmbientFileUpload = async (file: File) => {
        if (file.size > MAX_AMBIENT_NOISE_FILE_SIZE) {
            setAudioUploadError(`File too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum is 10MB.`);
            return;
        }

        setIsUploadingAudio(true);
        setAudioUploadError(null);

        try {
            // 1. Get presigned upload URL
            const res = await getAmbientNoiseUploadUrlApiV1WorkflowAmbientNoiseUploadUrlPost({
                body: {
                    workflow_id: Number(workflowId),
                    filename: file.name,
                    mime_type: file.type || "audio/wav",
                    file_size: file.size,
                },
            });

            if (res.error || !res.data?.upload_url) {
                throw new Error("Failed to get upload URL");
            }

            const data = res.data;

            // 2. Upload file to storage
            const uploadRes = await fetch(data.upload_url, {
                method: "PUT",
                body: file,
                headers: { "Content-Type": file.type || "audio/wav" },
            });
            if (!uploadRes.ok) {
                throw new Error("File upload failed");
            }

            // 3. Update config with storage reference
            setAmbientNoiseConfig((prev) => ({
                ...prev,
                storage_key: data.storage_key,
                storage_backend: data.storage_backend,
                original_filename: file.name,
            }));
        } catch (err) {
            setAudioUploadError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setIsUploadingAudio(false);
            if (ambientFileInputRef.current) ambientFileInputRef.current.value = "";
        }
    };

    const handleRemoveCustomAudio = () => {
        setAmbientNoiseConfig((prev) => ({
            enabled: prev.enabled,
            volume: prev.volume,
        }));
    };

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave(
                {
                    ...workflowConfigurations,
                    ambient_noise_configuration: ambientNoiseConfig,
                    max_call_duration: maxCallDuration,
                    max_user_idle_timeout: maxUserIdleTimeout,
                    smart_turn_stop_secs: smartTurnStopSecs,
                    turn_start_strategy: turnStartStrategy,
                    turn_start_min_words: turnStartMinWords,
                    provisional_vad_pause_secs: provisionalVadPauseSecs,
                    turn_stop_strategy: turnStopStrategy,
                    user_speech_timeout: userSpeechTimeout,
                    interruption_backoff_secs: interruptionBackoffSecs,
                    fallback_tts: fallbackTts,
                    fallback_stt: fallbackStt,
                    context_compaction_enabled: contextCompactionEnabled,
                    transcript_configuration: {
                        ...(workflowConfigurations.transcript_configuration ?? {}),
                        include_end_timestamps: includeTranscriptEndTimestamps,
                    },
                },
                name,
            );
        } catch (error) {
            console.error("Failed to save general settings:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="general">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Settings className="h-4 w-4" />
                    General
                </CardTitle>
                <CardDescription>Agent name, call behavior, and turn detection.{" "}
                    <a href={SETTINGS_DOCUMENTATION_URLS.general} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
                {/* Agent Name */}
                <div className="space-y-2">
                    <Label htmlFor="workflow_name" className="text-sm font-medium">Agent Name</Label>
                    <Input
                        id="workflow_name"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder="Enter Agent name"
                    />
                </div>

                <Separator />
                <SettingsGroup
                    title="Conversation"
                    blurb="How the agent takes turns, and when a caller can cut in."
                    defaultOpen={true}
                >
                {/* Response Rate */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Response Rate</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            How quickly the agent takes its turn. Writes the three
                            timings marked <span className="rounded border px-1 py-px text-[10px] leading-4">Response Rate</span> below;
                            which of them are shown depends on the strategies you pick.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <Select
                            value={activePreset}
                            onValueChange={(value) => {
                                if (value !== "custom") applyLatencyPreset(value as LatencyPreset);
                            }}
                        >
                            <SelectTrigger id="latency_preset">
                                <SelectValue placeholder="Select a response rate" />
                            </SelectTrigger>
                            <SelectContent>
                                {(Object.keys(LATENCY_PRESETS) as LatencyPreset[]).map((key) => (
                                    <SelectItem key={key} value={key}>
                                        {LATENCY_PRESETS[key].label}
                                        {key === "balanced" ? " (Recommended)" : ""}
                                    </SelectItem>
                                ))}
                                {/* Only reachable by moving a slider, so it is
                                    shown rather than offered. */}
                                {activePreset === "custom" && (
                                    <SelectItem value="custom">Custom</SelectItem>
                                )}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {activePreset === "custom"
                                ? "Your own combination of the timings below. Pick a preset to reset them."
                                : LATENCY_PRESETS[activePreset].blurb}
                        </p>
                    </div>
                </div>

                        <Separator />
                {/* Turn Detection */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Turn Detection</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Configure how the agent detects when the user has finished speaking.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="turn_stop_strategy" className="text-xs">Detection Strategy</Label>
                        <Select
                            value={turnStopStrategy}
                            onValueChange={(value: TurnStopStrategy) => setTurnStopStrategy(value)}
                        >
                            <SelectTrigger id="turn_stop_strategy">
                                <SelectValue placeholder="Select strategy" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="turn_analyzer">
                                    Smart Turn Analyzer (Recommended)
                                </SelectItem>
                                <SelectItem value="transcription">Silence timeout</SelectItem>
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {turnStopStrategy === "transcription"
                                ? "Waits a fixed silence after every turn, whether or not the caller had obviously finished. Predictable, but that wait is paid even on a one-word answer."
                                : "A local model judges whether the sentence sounds finished, and only waits out the silence below when it is unsure. Quick on a finished sentence, patient on an ambiguous one — about 12ms to decide."}
                        </p>
                    </div>
                    {turnStopStrategy === "turn_analyzer" && (
                        <Slider
                            id="smart_turn_stop_secs"
                            presetOf="Response Rate"
                            label="Incomplete Turn Timeout"
                            unit="s"
                            min={0.5}
                            max={10}
                            step={0.5}
                            value={smartTurnStopSecs}
                            onValueChange={setSmartTurnStopSecs}
                            hint="How long to wait when the analyzer is unsure the caller has finished. Only reached on an ambiguous turn. Default: 2s"
                        />
                    )}
                    {turnStopStrategy === "transcription" && (
                        <Slider
                            id="user_speech_timeout"
                            presetOf="Response Rate"
                            label="Endpointing Delay"
                            unit="s"
                            min={MIN_USER_SPEECH_TIMEOUT}
                            max={MAX_USER_SPEECH_TIMEOUT}
                            step={0.05}
                            value={userSpeechTimeout}
                            onValueChange={setUserSpeechTimeout}
                            hint={`Silence to wait after the caller stops, on top of the VAD's own 0.2s, before the turn ends. Paid on every turn, so it sets a floor under response time that no faster model can recover. Below ${MIN_USER_SPEECH_TIMEOUT}s it starts cutting people off mid-sentence. Ignored when the transcriber reports its own turn boundaries. Default: ${DEFAULT_USER_SPEECH_TIMEOUT}s`}
                        />
                    )}
                </div>

                        <Separator />
                {/* Interruption */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Interruption</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Configure when user speech should interrupt the agent while it is speaking.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="turn_start_strategy" className="text-xs">Interruption Strategy</Label>
                        <Select
                            value={turnStartStrategy}
                            onValueChange={(value: TurnStartStrategy) => setTurnStartStrategy(value)}
                        >
                            <SelectTrigger id="turn_start_strategy">
                                <SelectValue placeholder="Select strategy" />
                            </SelectTrigger>
                            <SelectContent>
                                {TURN_START_STRATEGY_OPTIONS.map((option) => (
                                    <SelectItem key={option.value} value={option.value}>
                                        {option.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        <p className="text-xs text-muted-foreground">
                            {selectedTurnStartStrategy?.description}
                        </p>
                    </div>
                    {turnStartStrategy === "min_words" && (
                        <Slider
                            id="turn_start_min_words"
                            presetOf="Response Rate"
                            label="Minimum Words Before Interruption"
                            unit=" words"
                            min={1}
                            max={10}
                            step={1}
                            value={turnStartMinWords}
                            onValueChange={setTurnStartMinWords}
                            hint={`Transcribed words needed to interrupt the agent. Raise it so a cough, a "mhm" or background speech no longer cuts the agent off mid-sentence. Default: ${DEFAULT_TURN_START_MIN_WORDS}`}
                        />
                    )}
                    <Slider
                        id="interruption_backoff_secs"
                        label="Pause After Being Interrupted"
                        unit="s"
                        min={0}
                        max={3}
                        step={0.1}
                        value={interruptionBackoffSecs}
                        onValueChange={setInterruptionBackoffSecs}
                        hint="How long to wait before the agent speaks again after the caller cuts in, so a short interruption is not answered before they have finished. Usually costs nothing: the caller finishing, the turn being detected and the reply being generated normally take longer than this. 0 turns it off entirely."
                    />
                    {turnStartStrategy === "provisional_vad" && (
                        <Slider
                            id="provisional_vad_pause_secs"
                            label="Provisional Pause"
                            unit="s"
                            min={0.1}
                            max={5}
                            step={0.1}
                            value={provisionalVadPauseSecs}
                            onValueChange={setProvisionalVadPauseSecs}
                            hint={`How long to pause the agent's audio while waiting for the transcript to confirm the caller really spoke. Default: ${DEFAULT_PROVISIONAL_VAD_PAUSE_SECS}s`}
                        />
                    )}
                </div>

                </SettingsGroup>
                <Separator />
                <SettingsGroup
                    title="Reliability and limits"
                    blurb="What happens when a provider fails, and when a call should end."
                    defaultOpen={true}
                >
                {/* Fallbacks */}
                <div className="space-y-5">
                    <div>
                        <h3 className="text-sm font-medium">Fallbacks</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Where the call goes if a provider fails while someone is on the
                            line. Tried in order, and only for a provider reporting a problem
                            it expects to survive — a provider saying the call cannot
                            continue still ends it.
                        </p>
                    </div>

                    <FallbackChain
                        label="Voice"
                        kind="tts"
                        description="A voice that stops mid-sentence is dead air, which is the worst thing a caller can be handed."
                        schemas={modelConfigurationDefaults?.byok?.pipeline?.tts}
                        value={fallbackTts}
                        onChange={setFallbackTts}
                    />

                    <FallbackChain
                        label="Transcriber"
                        kind="stt"
                        description="A transcriber that fails leaves the agent unable to hear, so it waits through a caller who is already talking."
                        schemas={modelConfigurationDefaults?.byok?.pipeline?.stt}
                        value={fallbackStt}
                        onChange={setFallbackStt}
                    />
                </div>

                        <Separator />
                {/* Call Management */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Call Management</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Configure call duration limits and idle timeout settings.
                        </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="max_call_duration" className="text-xs">Max Call Duration (seconds)</Label>
                            <Input
                                id="max_call_duration"
                                type="number"
                                min="1"
                                value={maxCallDuration}
                                onChange={(e) => {
                                    const value = parseInt(e.target.value);
                                    if (!isNaN(value) && value > 0) setMaxCallDuration(value);
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Default: 600 (10 minutes)</p>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="max_user_idle_timeout" className="text-xs">
                                Max User Idle Timeout (seconds)
                            </Label>
                            <Input
                                id="max_user_idle_timeout"
                                type="number"
                                min="1"
                                value={maxUserIdleTimeout}
                                onChange={(e) => {
                                    const value = parseInt(e.target.value);
                                    if (!isNaN(value) && value > 0) setMaxUserIdleTimeout(value);
                                }}
                            />
                            <p className="text-xs text-muted-foreground">Default: 10 seconds</p>
                        </div>
                    </div>
                </div>
                </SettingsGroup>
                <Separator />
                <SettingsGroup
                    title="Audio"
                    blurb="What the caller hears behind the agent."
                    defaultOpen={true}
                >
                {/* Ambient Noise */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Ambient Noise</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Add background ambient noise to make the conversation sound more natural.
                        </p>
                    </div>
                    <div className="flex items-center justify-between">
                        <Label htmlFor="ambient-noise-enabled" className="text-sm">Use Ambient Noise</Label>
                        <Switch
                            id="ambient-noise-enabled"
                            checked={ambientNoiseConfig.enabled}
                            onCheckedChange={(checked) =>
                                setAmbientNoiseConfig((prev) => ({ ...prev, enabled: checked }))
                            }
                        />
                    </div>
                    {ambientNoiseConfig.enabled && (
                        <div className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="ambient-volume" className="text-xs">Volume</Label>
                                <Input
                                    id="ambient-volume"
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="1"
                                    value={ambientNoiseConfig.volume}
                                    onChange={(e) => {
                                        const value = parseFloat(e.target.value);
                                        if (!isNaN(value)) setAmbientNoiseConfig((prev) => ({ ...prev, volume: value }));
                                    }}
                                />
                            </div>

                            {/* Custom Audio File */}
                            <div className="space-y-2">
                                <Label className="text-xs">Custom Audio File</Label>
                                <p className="text-xs text-muted-foreground">
                                    Upload your own audio file or use the default office ambience.
                                </p>

                                {ambientNoiseConfig.storage_key ? (
                                    <div className="flex items-center gap-2 rounded-md border p-2 bg-muted/10">
                                        <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate flex-1">
                                            {ambientNoiseConfig.original_filename || "Custom audio"}
                                        </code>
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="ghost"
                                            className="h-6 w-6 p-0 shrink-0"
                                            onClick={async () => {
                                                try {
                                                    await togglePlayback(
                                                        "ambient-noise",
                                                        ambientNoiseConfig.storage_key!,
                                                        ambientNoiseConfig.storage_backend,
                                                    );
                                                } catch {
                                                    setAudioUploadError("Failed to play audio");
                                                }
                                            }}
                                        >
                                            {playingId === "ambient-noise" ? (
                                                <Pause className="w-3.5 h-3.5" />
                                            ) : (
                                                <Play className="w-3.5 h-3.5" />
                                            )}
                                        </Button>
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="ghost"
                                            className="h-6 w-6 p-0 shrink-0"
                                            onClick={handleRemoveCustomAudio}
                                        >
                                            <X className="w-3.5 h-3.5" />
                                        </Button>
                                    </div>
                                ) : (
                                    <div>
                                        <input
                                            ref={ambientFileInputRef}
                                            type="file"
                                            accept="audio/*"
                                            onChange={(e) => {
                                                const file = e.target.files?.[0];
                                                if (file) handleAmbientFileUpload(file);
                                            }}
                                            className="hidden"
                                        />
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            className="text-sm font-normal"
                                            onClick={() => ambientFileInputRef.current?.click()}
                                            disabled={isUploadingAudio}
                                        >
                                            {isUploadingAudio ? (
                                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                            ) : (
                                                <Upload className="w-4 h-4 mr-2" />
                                            )}
                                            {isUploadingAudio ? "Uploading..." : "Upload audio file (max 10MB)"}
                                        </Button>
                                    </div>
                                )}

                                {audioUploadError && (
                                    <p className="text-xs text-destructive">{audioUploadError}</p>
                                )}

                                {!ambientNoiseConfig.storage_key && (
                                    <p className="text-xs text-muted-foreground italic">
                                        Using default office ambience
                                    </p>
                                )}
                            </div>
                        </div>
                    )}
                </div>
                </SettingsGroup>
                <Separator />
                <SettingsGroup
                    title="Advanced"
                    blurb="Transcript detail and how a long conversation is kept in context."
                    defaultOpen={advancedTouched}
                >
                {/* Transcript */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Transcript</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Include start and stop timestamps for each speaker in the uploaded transcript.
                        </p>
                    </div>
                    <div className="flex items-center justify-between">
                        <Label htmlFor="transcript-end-timestamps-enabled" className="text-sm">
                            Enhanced Timestamped Transcript
                        </Label>
                        <Switch
                            id="transcript-end-timestamps-enabled"
                            checked={includeTranscriptEndTimestamps}
                            onCheckedChange={setIncludeTranscriptEndTimestamps}
                        />
                    </div>
                    <div className="rounded-md border bg-muted/20 p-3">
                        <pre className="whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                            {`[2026-07-06T10:00:00.000Z -> 2026-07-06T10:00:04.800Z] assistant: Can you confirm your date of birth?
[2026-07-06T10:00:06.200Z -> 2026-07-06T10:00:08.700Z] user: January fifth, nineteen ninety.`}
                        </pre>
                    </div>
                </div>

                        <Separator />
                {/* Context Compaction */}
                <div className="space-y-4">
                    <div>
                        <h3 className="text-sm font-medium">Context Compaction</h3>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Automatically summarize conversation context when transitioning between nodes. Not applicable in Realtime mode - the speech-to-speech service manages its own conversation state and this setting is ignored.
                        </p>
                    </div>
                    <div className="flex items-center justify-between">
                        <Label htmlFor="context-compaction-enabled" className="text-sm">
                            Enable Context Compaction
                        </Label>
                        <Switch
                            id="context-compaction-enabled"
                            checked={contextCompactionEnabled}
                            onCheckedChange={setContextCompactionEnabled}
                        />
                    </div>
                </div>

                </SettingsGroup>
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save General Settings"}
                </Button>
            </CardFooter>
        </Card>
    );
}

/**
 * One collapsible group of related settings.
 *
 * The General card carried nine sections, all expanded, so finding one meant
 * scrolling past the other eight -- and Fallbacks, which is the difference
 * between a failed provider and dead air on a call, sat eighth.
 *
 * ``defaultOpen`` is a starting state rather than a fixed one, and a group
 * holding a value somebody has already changed passes true: a setting that is
 * not on its default must never be hidden behind a click nobody knew to make.
 * ServiceConfigurationForm's advanced block reaches the same conclusion.
 */
function SettingsGroup({
    title,
    blurb,
    defaultOpen,
    children,
}: {
    title: string;
    blurb: string;
    defaultOpen: boolean;
    children: React.ReactNode;
}) {
    const [open, setOpen] = useState(defaultOpen);

    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger className="flex w-full items-start gap-2 text-left">
                <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform data-[state=open]:rotate-90" />
                <span className="min-w-0">
                    <span className="block text-sm font-medium">{title}</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">{blurb}</span>
                </span>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-6 pt-6">{children}</CollapsibleContent>
        </Collapsible>
    );
}

// ---------------------------------------------------------------------------
// Section: Template Variables
// ---------------------------------------------------------------------------

function TemplateVariablesSection({
    templateContextVariables,
    onSave,
}: {
    templateContextVariables: Record<string, string>;
    onSave: (variables: Record<string, string>) => Promise<void>;
}) {
    const [contextVars, setContextVars] = useState<Record<string, string>>(templateContextVariables);
    const [newKey, setNewKey] = useState("");
    const [newValue, setNewValue] = useState("");
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        const pendingVars = newKey && newValue ? { ...contextVars, [newKey]: newValue } : contextVars;
        return JSON.stringify(pendingVars) !== JSON.stringify(templateContextVariables);
    }, [contextVars, newKey, newValue, templateContextVariables]);

    useUnsavedChanges("variables", isDirty);

    const handleAdd = () => {
        if (newKey && newValue) {
            setContextVars((prev) => ({ ...prev, [newKey]: newValue }));
        }
        setNewKey("");
        setNewValue("");
    };

    const handleRemove = (key: string) => {
        setContextVars((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
        });
    };

    const handleSave = async () => {
        setIsSaving(true);
        try {
            let varsToSave = contextVars;
            if (newKey && newValue) {
                varsToSave = { ...varsToSave, [newKey]: newValue };
            }
            await onSave(varsToSave);
        } catch (error) {
            console.error("Failed to save variables:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="variables">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Variable className="h-4 w-4" />
                    Template Variables
                </CardTitle>
                <CardDescription>
                    Variables available in workflow prompts via {`{{variable_name}}`} syntax for testing the workflow.{" "}
                    <a href={SETTINGS_DOCUMENTATION_URLS.templateVariables} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Existing Variables */}
                {Object.entries(contextVars).length > 0 && (
                    <div className="space-y-2">
                        <Label className="text-sm font-medium">Current Variables</Label>
                        {Object.entries(contextVars).map(([key, value]) => (
                            <div key={key} className="flex items-center gap-2 rounded-md border p-2">
                                <div className="flex-1 min-w-0">
                                    <div className="text-sm font-medium">{key}</div>
                                    <div className="text-xs text-muted-foreground truncate">{value}</div>
                                </div>
                                <Button size="sm" variant="ghost" onClick={() => handleRemove(key)}>
                                    <Trash2Icon className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                    </div>
                )}

                {/* Add New Variable */}
                <div className="space-y-3">
                    <Label className="text-sm font-medium">Add New Variable</Label>
                    <div className="flex gap-2">
                        <div className="flex-1 space-y-1">
                            <Label htmlFor="var-key" className="text-xs">Key</Label>
                            <Input
                                id="var-key"
                                placeholder="Enter variable key"
                                value={newKey}
                                onChange={(e) => setNewKey(e.target.value)}
                            />
                        </div>
                        <div className="flex-1 space-y-1">
                            <Label htmlFor="var-value" className="text-xs">Value</Label>
                            <Input
                                id="var-value"
                                placeholder="Enter variable value"
                                value={newValue}
                                onChange={(e) => setNewValue(e.target.value)}
                            />
                        </div>
                    </div>
                    <Button size="sm" onClick={handleAdd} disabled={!newKey || !newValue}>
                        Add Variable
                    </Button>
                </div>
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Variables"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Dictionary
// ---------------------------------------------------------------------------

function DictionarySection({
    dictionary,
    onSave,
}: {
    dictionary: string;
    onSave: (dictionary: string) => Promise<void>;
}) {
    const [dictionaryValue, setDictionaryValue] = useState(dictionary);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = dictionaryValue !== dictionary;

    useUnsavedChanges("dictionary", isDirty);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            await onSave(dictionaryValue);
        } catch (error) {
            console.error("Failed to save dictionary:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="dictionary">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <BookA className="h-4 w-4" />
                    Dictionary
                </CardTitle>
                <CardDescription>
                    Add words the agent should actively listen for &mdash; company jargon, names,
                    industry terms. May incur extra cost depending on provider.
                </CardDescription>
            </CardHeader>
            <CardContent>
                <Textarea
                    placeholder="Enter words separated by comma (e.g. billing department, tretinoin)"
                    value={dictionaryValue}
                    onChange={(e) => setDictionaryValue(e.target.value)}
                    rows={4}
                    className="resize-none"
                />
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Dictionary"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Voicemail Detection
// ---------------------------------------------------------------------------

function VoicemailSection({
    workflowConfigurations,
    workflowName,
    onSave,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const getConfig = (): VoicemailDetectionConfiguration => ({
        ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
        ...workflowConfigurations.voicemail_detection,
    });

    const [enabled, setEnabled] = useState(getConfig().enabled);
    const [useWorkflowLlm, setUseWorkflowLlm] = useState(getConfig().use_workflow_llm);
    const [provider, setProvider] = useState(getConfig().provider || "openai");
    const [model, setModel] = useState(getConfig().model || "gpt-4.1");
    const [apiKey, setApiKey] = useState(getConfig().api_key || "");
    const [systemPrompt, setSystemPrompt] = useState(getConfig().system_prompt || DEFAULT_VOICEMAIL_SYSTEM_PROMPT);
    const [longSpeechTimeout, setLongSpeechTimeout] = useState(getConfig().long_speech_timeout);
    const [isSaving, setIsSaving] = useState(false);

    const isDirty = useMemo(() => {
        const init = {
            ...DEFAULT_VOICEMAIL_DETECTION_CONFIGURATION,
            ...workflowConfigurations.voicemail_detection,
        };
        return (
            enabled !== init.enabled ||
            useWorkflowLlm !== init.use_workflow_llm ||
            provider !== (init.provider || "openai") ||
            model !== (init.model || "gpt-4.1") ||
            apiKey !== (init.api_key || "") ||
            systemPrompt !== (init.system_prompt || DEFAULT_VOICEMAIL_SYSTEM_PROMPT) ||
            longSpeechTimeout !== init.long_speech_timeout
        );
    }, [enabled, useWorkflowLlm, provider, model, apiKey, systemPrompt, longSpeechTimeout, workflowConfigurations]);

    useUnsavedChanges("voicemail", isDirty);

    const handleSave = async () => {
        setIsSaving(true);
        try {
            const voicemailConfig: VoicemailDetectionConfiguration = {
                enabled,
                use_workflow_llm: useWorkflowLlm,
                provider: useWorkflowLlm ? undefined : provider,
                model: useWorkflowLlm ? undefined : model,
                api_key: useWorkflowLlm ? undefined : apiKey,
                system_prompt:
                    systemPrompt && systemPrompt !== DEFAULT_VOICEMAIL_SYSTEM_PROMPT ? systemPrompt : undefined,
                long_speech_timeout: longSpeechTimeout,
            };
            await onSave(
                { ...workflowConfigurations, voicemail_detection: voicemailConfig },
                workflowName,
            );
        } catch (error) {
            console.error("Failed to save voicemail settings:", error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card id="voicemail">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <PhoneOff className="h-4 w-4" />
                    Voicemail Detection
                </CardTitle>
                <CardDescription>
                    Automatically detect and end calls when a voicemail system is reached.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="flex items-center space-x-2 rounded-md border bg-muted/20 p-2">
                    <Switch id="voicemail-enabled" checked={enabled} onCheckedChange={setEnabled} />
                    <Label htmlFor="voicemail-enabled">Enable Voicemail Detection</Label>
                </div>

                {enabled && (
                    <>
                        {/* LLM Configuration */}
                        <div className="space-y-3">
                            <div className="flex items-center space-x-2 rounded-md border bg-muted/20 p-2">
                                <Switch
                                    id="voicemail-use-workflow-llm"
                                    checked={useWorkflowLlm}
                                    onCheckedChange={setUseWorkflowLlm}
                                />
                                <Label htmlFor="voicemail-use-workflow-llm">Use Workflow LLM</Label>
                                <Label className="ml-2 text-xs text-muted-foreground">
                                    Use the LLM configured in your account settings.
                                </Label>
                            </div>

                            {!useWorkflowLlm && (
                                <LLMConfigSelector
                                    provider={provider}
                                    onProviderChange={setProvider}
                                    model={model}
                                    onModelChange={setModel}
                                    apiKey={apiKey}
                                    onApiKeyChange={setApiKey}
                                />
                            )}
                        </div>

                        {/* System Prompt */}
                        <div className="space-y-2">
                            <Label>System Prompt</Label>
                            <p className="text-xs text-muted-foreground">
                                The LLM must respond with either &quot;CONVERSATION&quot; or &quot;VOICEMAIL&quot;.
                            </p>
                            <Textarea
                                value={systemPrompt}
                                onChange={(e) => setSystemPrompt(e.target.value)}
                                className="min-h-[200px] font-mono text-xs"
                            />
                        </div>

                        {/* Timing */}
                        <div className="space-y-2 rounded-md border bg-muted/10 p-3">
                            <Label className="font-medium">Timing</Label>
                            <div className="space-y-2">
                                <Label className="text-sm">Speech Cutoff (seconds)</Label>
                                <p className="text-xs text-muted-foreground">
                                    Trigger classification early if first turn speech exceeds this duration.
                                </p>
                                <Input
                                    type="number"
                                    step="0.5"
                                    min="1"
                                    max="30"
                                    value={longSpeechTimeout}
                                    onChange={(e) => setLongSpeechTimeout(parseFloat(e.target.value) || 8.0)}
                                />
                            </div>
                        </div>
                    </>
                )}
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty}>
                    {isSaving ? "Saving..." : "Save Voicemail Settings"}
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Agent UUID
// ---------------------------------------------------------------------------

function AgentUuidSection({ workflowUuid }: { workflowUuid: string }) {
    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(workflowUuid);
            toast.success("Agent UUID copied");
        } catch {
            toast.error("Failed to copy Agent UUID");
        }
    };

    return (
        <Card id="identity">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Fingerprint className="h-4 w-4" />
                    Agent UUID
                </CardTitle>
                <CardDescription>
                    Stable identifier for this agent. Used in agent-stream URLs and
                    other integrations where a numeric workflow ID isn&apos;t portable.
                </CardDescription>
            </CardHeader>
            <CardContent>
                <button
                    type="button"
                    onClick={handleCopy}
                    title="Click to copy"
                    className="group flex w-full items-center gap-2 rounded-md border bg-muted/20 p-2 text-left font-mono text-xs transition-colors hover:bg-muted/40"
                >
                    <code className="flex-1 truncate">{workflowUuid}</code>
                    <Clipboard className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-foreground" />
                </button>
            </CardContent>
            <CardFooter className="border-t pt-6">
                <Button variant="outline" size="sm" onClick={handleCopy}>
                    <Clipboard className="h-3.5 w-3.5 mr-2" />
                    Copy UUID
                </Button>
            </CardFooter>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Section: Model Overrides
// ---------------------------------------------------------------------------

function withoutModelConfigurationOverrides(configurations: WorkflowConfigurations): WorkflowConfigurations {
    const next = { ...configurations };
    delete next.model_overrides;
    delete next.model_configuration_v2_override;
    return next;
}

function WorkflowModelOverridesSection({
    workflowConfigurations,
    workflowName,
    onSave,
    modelConfigurationDefaults,
    organizationModelConfiguration,
    modelConfigurationLoading,
    modelConfigurationError,
}: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
    modelConfigurationDefaults: ModelConfigurationDefaultsV2 | null;
    organizationModelConfiguration: OrganizationAiModelConfigurationResponse | null;
    modelConfigurationLoading: boolean;
    modelConfigurationError: string | null;
}) {
    const savedV2Override = workflowConfigurations.model_configuration_v2_override;
    const hasSavedModelOverride = Boolean(savedV2Override || workflowConfigurations.model_overrides);
    const [overrideEnabled, setOverrideEnabled] = useState(Boolean(savedV2Override));
    const [isRemovingOverride, setIsRemovingOverride] = useState(false);

    useEffect(() => {
        setOverrideEnabled(Boolean(workflowConfigurations.model_configuration_v2_override));
    }, [workflowConfigurations.model_configuration_v2_override]);

    const hasOrgConfiguration = organizationModelConfiguration?.source === "organization_v2";

    const saveV2Override = async (configuration: OrganizationAiModelConfigurationV2) => {
        const nextConfigurations = withoutModelConfigurationOverrides(workflowConfigurations);
        nextConfigurations.model_configuration_v2_override = configuration;
        await onSave(nextConfigurations, workflowName);
        toast.success("Model override saved");
    };

    const removeV2Override = async () => {
        setIsRemovingOverride(true);
        try {
            await onSave(withoutModelConfigurationOverrides(workflowConfigurations), workflowName);
            setOverrideEnabled(false);
            toast.success("Using organization model configuration");
        } finally {
            setIsRemovingOverride(false);
        }
    };

    return (
        <Card id="models">
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Brain className="h-4 w-4" />
                    Models
                </CardTitle>
                <CardDescription>
                    This agent runs on the organization&apos;s models unless you give it its
                    own. Each model can be provided by Decibyl or run on your own key.{" "}
                    <a href={SETTINGS_DOCUMENTATION_URLS.modelOverrides} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {modelConfigurationLoading && (
                    <div className="flex items-center gap-2 rounded-md border p-4 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading model configuration
                    </div>
                )}

                {modelConfigurationError && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {modelConfigurationError}
                    </div>
                )}

                {!modelConfigurationLoading && !modelConfigurationError && !hasOrgConfiguration && (
                    <div className="flex flex-col gap-3 rounded-md border bg-muted/30 p-4 sm:flex-row sm:items-center sm:justify-between">
                        <p className="text-sm text-muted-foreground">
                            Set up your organization model configuration before overriding it per workflow.
                        </p>
                        <Button type="button" variant="outline" size="sm" asChild>
                            <Link href="/model-configurations">Configure Models</Link>
                        </Button>
                    </div>
                )}

                {!modelConfigurationLoading && !modelConfigurationError && hasOrgConfiguration && modelConfigurationDefaults && organizationModelConfiguration && (
                    <>
                        {/* What a minute of this agent costs, before anyone dials it.
                            Priced from the stack the agent will actually run: its own
                            override where it has one, the organization default where it
                            does not. Telephony is left out deliberately -- the carrier
                            is chosen per call, not per agent, so a number here would be
                            a guess presented as a rate. */}
                        <CostPerMinuteBar
                            carriageNote="Telephony is not included: the carrier is chosen when the call is placed."
                            stack={pricedStack(
                                savedV2Override
                                    ? priceableFromV2(savedV2Override)
                                    : (organizationModelConfiguration.effective_configuration as
                                          | Parameters<typeof pricedStack>[0]
                                          | null),
                                modelConfigurationDefaults.decibyl?.upstream,
                            )}
                        />

                        <div className="flex items-center justify-between rounded-md border p-4">
                            <div className="space-y-0.5">
                                <Label htmlFor="workflow-model-v2-override" className="text-sm font-medium">
                                    Give this agent its own models
                                </Label>
                                <p className="text-xs text-muted-foreground">
                                    {overrideEnabled
                                        ? "This agent has its own stack. Changes to the organization default no longer reach it."
                                        : "Inheriting the organization default. Turn this on to pick models for this agent alone."}
                                </p>
                            </div>
                            <Switch
                                id="workflow-model-v2-override"
                                checked={overrideEnabled}
                                onCheckedChange={setOverrideEnabled}
                            />
                        </div>

                        {overrideEnabled ? (
                            <AIModelConfigurationV2Editor
                                defaults={modelConfigurationDefaults}
                                configuration={
                                    (savedV2Override as OrganizationAiModelConfigurationV2 | undefined)
                                    || (organizationModelConfiguration.configuration as OrganizationAiModelConfigurationV2 | null)
                                }
                                effectiveConfiguration={
                                    savedV2Override
                                        ? null
                                        : organizationModelConfiguration.effective_configuration
                                }
                                submitLabel="Save Model Override"
                                onSave={saveV2Override}
                            />
                        ) : (
                            <div className="rounded-md border bg-muted/20 p-4">
                                <p className="text-sm text-muted-foreground">
                                    Using organization model configuration.
                                </p>
                                {hasSavedModelOverride && (
                                    <Button
                                        type="button"
                                        variant="outline"
                                        className="mt-3"
                                        onClick={removeV2Override}
                                        disabled={isRemovingOverride}
                                    >
                                        {isRemovingOverride ? "Saving..." : "Save Organization Configuration"}
                                    </Button>
                                )}
                            </div>
                        )}
                    </>
                )}
            </CardContent>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Page wrapper — handles auth & data fetching, then mounts the content
// component only when everything is loaded. This avoids useWorkflowState
// running with empty initial values and overwriting the Zustand store.
// ---------------------------------------------------------------------------

export default function WorkflowSettingsPage() {
    const params = useParams();
    const { user, redirectToLogin, loading: authLoading } = useAuth();
    const [workflow, setWorkflow] = useState<WorkflowResponse | undefined>(undefined);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authLoading && !user) {
            redirectToLogin();
        }
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        const fetchWorkflow = async () => {
            if (!user) return;
            try {
                const response = await getWorkflowApiV1WorkflowFetchWorkflowIdGet({
                    path: { workflow_id: Number(params.workflowId) },
                });
                setWorkflow(response.data);
            } catch (err) {
                setError("Failed to fetch workflow");
                logger.error(`Error fetching workflow settings: ${err}`);
            } finally {
                setLoading(false);
            }
        };
        if (user) fetchWorkflow();
    }, [params.workflowId, user]);

    if (loading || authLoading) return <SpinLoader />;

    if (error || !workflow) {
        return (
            <div className="flex min-h-screen items-center justify-center">
                <div className="text-lg text-destructive">{error || "Workflow not found"}</div>
            </div>
        );
    }

    if (!user) return null;

    return <WorkflowSettingsContent workflow={workflow} user={user} />;
}

// ---------------------------------------------------------------------------
// Content — only mounts once the workflow API response is available, so
// useWorkflowState always initialises with real data.
// ---------------------------------------------------------------------------

function WorkflowSettingsContent({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    return (
        <UnsavedChangesProvider>
            <WorkflowSettingsInner workflow={workflow} user={user} />
        </UnsavedChangesProvider>
    );
}

function WorkflowSettingsInner({
    workflow,
    user,
}: {
    workflow: WorkflowResponse;
    user: { id: string; email?: string };
}) {
    const router = useRouter();
    const { dirtySections, confirmNavigate } = useUnsavedChangesContext();

    // Read once from the URL so a link can land on a tab -- the wizard's
    // "Advanced setup" and the docs both want to point at one -- and written
    // back with replaceState rather than the router so switching tabs does not
    // stack history entries a Back press has to walk out of.
    const [activeTab, setActiveTab] = useState<TabId>(DEFAULT_TAB);

    useEffect(() => {
        const requested = new URLSearchParams(window.location.search).get("tab");
        if (isTabId(requested)) setActiveTab(requested);
    }, []);

    const selectTab = useCallback((next: TabId) => {
        setActiveTab(next);
        const url = new URL(window.location.href);
        url.searchParams.set("tab", next);
        window.history.replaceState(null, "", url);
    }, []);
    const [modelConfigurationDefaults, setModelConfigurationDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const [organizationModelConfiguration, setOrganizationModelConfiguration] = useState<OrganizationAiModelConfigurationResponse | null>(null);
    const [modelConfigurationLoading, setModelConfigurationLoading] = useState(true);
    const [modelConfigurationError, setModelConfigurationError] = useState<string | null>(null);
    const hasFetchedModelConfiguration = useRef(false);

    const workflowId = workflow.id;

    const initialFlow = useMemo(
        () => ({
            nodes: workflow.workflow_definition.nodes as FlowNode[],
            edges: workflow.workflow_definition.edges as FlowEdge[],
            viewport: { x: 0, y: 0, zoom: 0 },
        }),
        [workflow],
    );

    const initialTemplateContextVariables = useMemo(
        () => (workflow.template_context_variables as Record<string, string>) || {},
        [workflow],
    );

    const initialWorkflowConfigurations = useMemo(
        () => (
            workflow.workflow_configurations
                ? (workflow.workflow_configurations as WorkflowConfigurations)
                : undefined
        ),
        [workflow],
    );

    const {
        workflowName,
        workflowConfigurations,
        templateContextVariables,
        dictionary,
        saveWorkflowConfigurations,
        saveTemplateContextVariables,
        saveDictionary,
    } = useWorkflowState({
        initialWorkflowName: workflow.name,
        workflowId,
        initialFlow,
        initialTemplateContextVariables,
        initialWorkflowConfigurations,
        user,
    });
    const resolvedWorkflowConfigurationsForRender = workflowConfigurations
        ? resolveWorkflowConfigurations(workflowConfigurations)
        : null;

    useEffect(() => {
        if (hasFetchedModelConfiguration.current) return;
        hasFetchedModelConfiguration.current = true;

        const loadModelConfiguration = async () => {
            setModelConfigurationLoading(true);
            setModelConfigurationError(null);
            const [defaultsResult, configurationResult] = await Promise.all([
                getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
                getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get(),
            ]);

            if (defaultsResult.error) {
                setModelConfigurationError(detailFromResult(defaultsResult, "Failed to load model configuration defaults"));
                setModelConfigurationLoading(false);
                return;
            }
            if (configurationResult.error) {
                setModelConfigurationError(detailFromResult(configurationResult, "Failed to load model configuration"));
                setModelConfigurationLoading(false);
                return;
            }

            setModelConfigurationDefaults(defaultsResult.data as ModelConfigurationDefaultsV2);
            setOrganizationModelConfiguration(configurationResult.data || null);
            setModelConfigurationLoading(false);
        };

        loadModelConfiguration();
    }, []);

    return (
        <div className="min-h-screen">
            {/* Sticky header */}
            <header className="sticky top-0 z-10 flex items-center gap-3 border-b bg-background/95 px-6 py-3 backdrop-blur supports-[backdrop-filter]:bg-background/60">
                <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => confirmNavigate(() => router.push(`/workflow/${workflowId}`))}
                >
                    <ArrowLeft className="h-4 w-4" />
                </Button>
                <div className="min-w-0">
                    <p className="text-xs text-muted-foreground">Workflow Settings</p>
                    <h1 className="truncate text-sm font-semibold">{workflowName || workflow.name}</h1>
                </div>

                {/* The strip lives in the sticky header so it stays reachable
                    from the bottom of a long tab -- Conversation alone is
                    several screens. Scrolls on a phone rather than wrapping
                    into a second row that would double the header's height. */}
                <div
                    role="tablist"
                    aria-label="Settings sections"
                    className="-mb-3 ml-auto flex min-w-0 gap-1 overflow-x-auto pb-1"
                >
                    {TABS.map((tab) => {
                        const Icon = tab.icon;
                        const unsaved = tab.sections.some((id) => dirtySections.has(id));
                        return (
                            <button
                                key={tab.id}
                                role="tab"
                                type="button"
                                aria-selected={activeTab === tab.id}
                                onClick={() => selectTab(tab.id)}
                                className={cn(
                                    "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors",
                                    activeTab === tab.id
                                        ? "bg-muted font-medium text-foreground"
                                        : "text-muted-foreground hover:text-foreground",
                                )}
                            >
                                <Icon className="h-3.5 w-3.5" />
                                {tab.label}
                                {/* A tab hiding an unsaved edit has to say so,
                                    or switching away looks like discarding. */}
                                {unsaved && (
                                    <span
                                        className="h-1.5 w-1.5 rounded-full bg-orange-500"
                                        aria-label="Unsaved changes"
                                    />
                                )}
                            </button>
                        );
                    })}
                </div>
            </header>

            {/* The agent-level strip above the settings-level one: which agent
                you are in, then which part of it. Analysis and Advanced both
                land here, so the page says which of the two is showing. */}
            <AgentTabs
                workflowId={workflowId}
                settingsGroup={activeTab === "analysis" ? "analysis" : "advanced"}
            />

            <div className="mx-auto max-w-4xl px-6 py-8">
                <div className="min-w-0 space-y-8">
                    {resolvedWorkflowConfigurationsForRender && (
                        <>
                            <div
                                className={cn("space-y-8", activeTab !== "calling" && "hidden")}
                            >
                            {/* General */}
                            <GeneralSection
                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                workflowName={workflowName || workflow.name}
                                workflowId={workflowId}
                                onSave={saveWorkflowConfigurations}
                                modelConfigurationDefaults={modelConfigurationDefaults}
                            />

                            {/* Voicemail Detection */}
                            <VoicemailSection
                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                workflowName={workflowName}
                                onSave={saveWorkflowConfigurations}
                            />
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "models" && "hidden")}
                            >
                            <WorkflowModelOverridesSection
                                workflowConfigurations={resolvedWorkflowConfigurationsForRender}
                                workflowName={workflowName}
                                onSave={saveWorkflowConfigurations}
                                modelConfigurationDefaults={modelConfigurationDefaults}
                                organizationModelConfiguration={organizationModelConfiguration}
                                modelConfigurationLoading={modelConfigurationLoading}
                                modelConfigurationError={modelConfigurationError}
                            />

                            {/* Dictionary sits with Models rather than alone:
                                it is words the transcriber should listen for,
                                which is a property of the ears, not a topic. */}
                            <DictionarySection dictionary={dictionary} onSave={saveDictionary} />
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "analysis" && "hidden")}
                            >
                            {/* Whether calls get reviewed at all. First on the
                                tab because it is the reason the tab used to
                                look empty: QA has been built for months and
                                ran on almost nothing, since no creation path
                                added the node the runtime looks for. Every
                                path does now, so for a new agent this reads as
                                on; it stays the control for older ones. */}
                            <QaCard workflowId={workflowId} />

                            {/* Recordings – moved to org-level page */}
                            <Card id="recordings">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Mic className="h-4 w-4" />
                                        Recordings
                                    </CardTitle>
                                    <CardDescription>
                                        Recordings are now managed at the organization level and shared across all agents.
                                        Use <code className="rounded bg-muted px-1 text-xs">@</code> in prompt fields to insert them.{" "}
                                        <a href={SETTINGS_DOCUMENTATION_URLS.recordings} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    <Button variant="outline" asChild>
                                        <Link href="/recordings">
                                            Go to Recordings
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </Link>
                                    </Button>
                                </CardFooter>
                            </Card>

                            {/* Report */}
                            <ReportSection workflowId={workflowId} />
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "deploy" && "hidden")}
                            >
                            {/* Deployment (dialog trigger) */}
                            <Card id="deployment">
                                <CardHeader>
                                    <CardTitle className="flex items-center gap-2 text-base">
                                        <Rocket className="h-4 w-4" />
                                        Add to Website
                                    </CardTitle>
                                    <CardDescription>
                                        Configure a widget to add this voice agent to your website.{" "}
                                        <a href={SETTINGS_DOCUMENTATION_URLS.deployment} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">Learn more <ExternalLink className="h-3 w-3" /></a>
                                    </CardDescription>
                                </CardHeader>
                                <CardFooter className="border-t pt-6">
                                    {/* A link, not a modal. The configurator now
                                        has its own screen under DEPLOY, where
                                        somebody who did not build this agent can
                                        find it. ?agent= carries the choice across
                                        so arriving from here skips the picker. */}
                                    <Button variant="outline" asChild>
                                        <Link href={`/deploy/web-widget?agent=${workflowId}`}>
                                            Configure Widget
                                            <ExternalLink className="ml-2 h-4 w-4" />
                                        </Link>
                                    </Button>
                                </CardFooter>
                            </Card>
                            </div>

                            <div
                                className={cn("space-y-8", activeTab !== "advanced" && "hidden")}
                            >
                            {/* Template Variables */}
                            <TemplateVariablesSection
                                templateContextVariables={templateContextVariables}
                                onSave={saveTemplateContextVariables}
                            />

                            {/* Agent UUID */}
                            {workflow.workflow_uuid && (
                                <AgentUuidSection workflowUuid={workflow.workflow_uuid} />
                            )}
                            </div>
                        </>
                    )}
                </div>

            </div>

        </div>
    );
}
