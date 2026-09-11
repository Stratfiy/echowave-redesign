"use client";

/**
 * What lives behind each tile's pencil, below the list of models.
 *
 * Vapi puts a slot's settings on the slot: the transcriber's panel carries
 * languages, turn-taking and keywords; the model's carries temperature; the
 * voice's carries speed and background sound. Ours had all of it on a
 * "Calling" tab as one long card, a tab away from the tiles that name what
 * it tunes. These are the same settings, moved next to the thing they
 * belong to, and nowhere else.
 *
 * Two kinds of value are edited here and they are saved differently. The
 * agent's call configuration (turn-taking, keywords, fillers, background
 * sound) is a patch on the workflow. The slot's own knobs (temperature,
 * reply length, speed) are stored on the model slot and travel with the
 * model, so switching vendors does not carry a speed the new one cannot
 * take. The panel edits both; the editor that hosts it saves each through
 * its own route.
 */

import { ChevronRight, Loader2, Pause, Play, Plus, Trash2, Upload, X } from "lucide-react";
import { useRef, useState } from "react";

import { getAmbientNoiseUploadUrlApiV1WorkflowAmbientNoiseUploadUrlPost } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useAudioPlayback } from "@/hooks/useAudioPlayback";
import { cn } from "@/lib/utils";
import {
    BACKCHANNEL_DEFAULT_DELAY_SECS,
    DEFAULT_PROVISIONAL_VAD_PAUSE_SECS,
    DEFAULT_TURN_START_MIN_WORDS,
    DEFAULT_USER_SPEECH_TIMEOUT,
    MAX_USER_SPEECH_TIMEOUT,
    MIN_USER_SPEECH_TIMEOUT,
    NOISE_SUPPRESSION_MAX_LEVEL,
    NOISE_SUPPRESSION_MIN_LEVEL,
    type PronunciationEntry,
    TURN_START_STRATEGY_OPTIONS,
    type TurnStartStrategy,
    type TurnStopStrategy,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

/** The knobs stored on the slot itself, beside the model. */
export type SlotTuning = {
    temperature?: number;
    max_tokens?: number;
    speed?: number;
};

export type PanelProps = {
    workflowId: number;
    /** The configuration as it would be if saved now: stored values under the draft. */
    config: WorkflowConfigurations;
    onChange: (patch: Partial<WorkflowConfigurations>) => void;
    tuning: SlotTuning;
    onTuning: (patch: SlotTuning) => void;
};

/**
 * The languages a line in India is actually asked to serve. Bare subtags,
 * which is what the stored field takes and what the live agents carry.
 */
export const AGENT_LANGUAGE_OPTIONS = [
    { value: "en", label: "English" },
    { value: "hi", label: "Hindi" },
    { value: "ta", label: "Tamil" },
    { value: "te", label: "Telugu" },
    { value: "kn", label: "Kannada" },
    { value: "ml", label: "Malayalam" },
    { value: "mr", label: "Marathi" },
    { value: "bn", label: "Bengali" },
    { value: "gu", label: "Gujarati" },
    { value: "pa", label: "Punjabi" },
] as const;

/**
 * Slider bounds that every vendor on the catalogue accepts. A speed of 1.8
 * is legal for one voice vendor and refused by the next; staying inside the
 * intersection means a value chosen here never fails on save.
 */
export const TEMPERATURE_RANGE = { min: 0.1, max: 1.5, step: 0.1 } as const;
export const SPEED_RANGE = { min: 0.6, max: 1.5, step: 0.05 } as const;
export const MAX_TOKENS_RANGE = { min: 16, max: 4096 } as const;

const MAX_AMBIENT_NOISE_FILE_SIZE = 10 * 1024 * 1024;

function linesToList(text: string): string[] {
    return text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
}

/** One setting: a heading, a sentence, and the control beside or below it. */
function Setting({
    title,
    blurb,
    control,
    children,
}: {
    title: string;
    blurb: string;
    /** A switch or a short control shown on the heading's row. */
    control?: React.ReactNode;
    children?: React.ReactNode;
}) {
    return (
        <div className="space-y-3 border-b border-border px-4 py-4 last:border-b-0">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <p className="text-sm font-medium">{title}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{blurb}</p>
                </div>
                {control && <div className="shrink-0">{control}</div>}
            </div>
            {children}
        </div>
    );
}

/** The fold at the bottom of every panel, open when something in it is off its default. */
function Advanced({ open, children }: { open: boolean; children: React.ReactNode }) {
    const [isOpen, setIsOpen] = useState(open);
    return (
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
            <CollapsibleTrigger className="flex w-full items-center gap-2 border-y border-border bg-muted/30 px-4 py-2.5 text-left text-sm font-medium">
                <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground transition-transform data-[state=open]:rotate-90" />
                Advanced
            </CollapsibleTrigger>
            <CollapsibleContent>{children}</CollapsibleContent>
        </Collapsible>
    );
}

// ---------------------------------------------------------------------------
// Transcriber
// ---------------------------------------------------------------------------

export function TranscriberPanel({ config, onChange }: PanelProps) {
    // Typed defensively: the generated shape lists it, but the app's own
    // configuration type widens unknown keys, and a stored null has to read
    // as "any language" rather than crash the panel.
    const languages: string[] = Array.isArray(config.agent_languages)
        ? (config.agent_languages as string[])
        : [];
    const suppression = config.noise_suppression_configuration ?? {
        enabled: true,
        level: NOISE_SUPPRESSION_MAX_LEVEL,
    };
    const stopStrategy = config.turn_stop_strategy;
    const startStrategy = config.turn_start_strategy;
    const startOption = TURN_START_STRATEGY_OPTIONS.find((o) => o.value === startStrategy);

    const toggleLanguage = (code: string) => {
        const next = languages.includes(code)
            ? languages.filter((l) => l !== code)
            : [...languages, code];
        onChange({ agent_languages: next });
    };

    const advancedTouched =
        startStrategy !== "default" ||
        (config.interruption_backoff_secs ?? 0) !== 0 ||
        Boolean(config.accept_keypad_input);

    return (
        <div>
            <Setting
                title="Languages"
                blurb={
                    languages.length === 0
                        ? "Any. The transcriber guesses the language of every utterance; naming the two or three this line serves turns a wrong guess into noise."
                        : "Only these. A short utterance heard as some other language is ignored rather than answered in it."
                }
            >
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Languages">
                    {AGENT_LANGUAGE_OPTIONS.map((option) => {
                        const on = languages.includes(option.value);
                        return (
                            <button
                                key={option.value}
                                type="button"
                                aria-pressed={on}
                                onClick={() => toggleLanguage(option.value)}
                                className={cn(
                                    "rounded-full border px-2.5 py-1 text-xs transition-colors",
                                    on
                                        ? "border-[var(--accent-brand)] bg-[var(--accent-brand-soft)] font-medium"
                                        : "border-border text-muted-foreground hover:text-foreground",
                                )}
                            >
                                {option.label}
                            </button>
                        );
                    })}
                </div>
                <div className="flex items-center justify-between pt-1">
                    <Label htmlFor="follow-caller-language" className="text-xs">
                        Follow the caller when they switch language
                    </Label>
                    <Switch
                        id="follow-caller-language"
                        checked={config.follow_caller_language ?? false}
                        onCheckedChange={(v) => onChange({ follow_caller_language: v })}
                    />
                </div>
            </Setting>

            <Setting
                title="Where the caller is"
                blurb="How loud and how clearly a sound must be speech before the agent treats it as the caller starting to talk. On a crowded line this stops the next table interrupting the agent mid-sentence."
                control={
                    <Select
                        value={config.caller_environment ?? "normal"}
                        onValueChange={(v) =>
                            onChange({
                                caller_environment: v as "quiet" | "normal" | "noisy",
                            })
                        }
                    >
                        <SelectTrigger
                            className="h-8 w-[150px] text-xs"
                            aria-label="Where the caller is"
                        >
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="quiet">Somewhere quiet</SelectItem>
                            <SelectItem value="normal">Normal</SelectItem>
                            <SelectItem value="noisy">Somewhere noisy</SelectItem>
                        </SelectContent>
                    </Select>
                }
            />

            <Setting
                title="Turn-taking"
                blurb={
                    stopStrategy === "turn_analyzer"
                        ? "A local model judges whether the sentence sounds finished, and only waits out the silence below when unsure."
                        : "Waits a fixed silence after every turn, whether or not the caller had obviously finished."
                }
                control={
                    <Select
                        value={stopStrategy}
                        onValueChange={(v: TurnStopStrategy) => onChange({ turn_stop_strategy: v })}
                    >
                        <SelectTrigger className="h-8 w-[150px] text-xs" aria-label="Turn detection">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="turn_analyzer">Smart</SelectItem>
                            <SelectItem value="transcription">Silence timeout</SelectItem>
                        </SelectContent>
                    </Select>
                }
            >
                {stopStrategy === "turn_analyzer" ? (
                    <Slider
                        id="smart_turn_stop_secs"
                        label="End-of-turn wait when unsure"
                        unit="s"
                        min={0.5}
                        max={10}
                        step={0.5}
                        value={config.smart_turn_stop_secs}
                        onValueChange={(v) => onChange({ smart_turn_stop_secs: v })}
                        hint="Only reached on an ambiguous turn. Default: 2s"
                    />
                ) : (
                    <Slider
                        id="user_speech_timeout"
                        label="End-of-turn wait"
                        unit="s"
                        min={MIN_USER_SPEECH_TIMEOUT}
                        max={MAX_USER_SPEECH_TIMEOUT}
                        step={0.05}
                        value={config.user_speech_timeout}
                        onValueChange={(v) => onChange({ user_speech_timeout: v })}
                        hint={`Silence after the caller stops before the agent replies. Paid on every turn. Default: ${DEFAULT_USER_SPEECH_TIMEOUT}s`}
                    />
                )}
            </Setting>

            <Setting
                title="Keywords"
                blurb="Names, jargon and product terms the transcriber should listen for. May cost extra with some vendors."
            >
                <Textarea
                    id="dictionary"
                    rows={2}
                    value={config.dictionary ?? ""}
                    onChange={(e) => onChange({ dictionary: e.target.value })}
                    placeholder="billing department, tretinoin, Narayani"
                    className="resize-none text-sm"
                />
            </Setting>

            <Setting
                title="Background denoising"
                blurb={
                    suppression.enabled
                        ? "Strips road and shop noise out of what the caller sends. About 20ms a turn."
                        : "Off. Calls are passed through as the carrier sends them."
                }
                control={
                    <Switch
                        id="noise-suppression-enabled"
                        aria-label="Background denoising"
                        checked={suppression.enabled}
                        onCheckedChange={(v) =>
                            onChange({
                                noise_suppression_configuration: { ...suppression, enabled: v },
                            })
                        }
                    />
                }
            >
                {suppression.enabled && (
                    <Slider
                        id="noise-suppression-level"
                        label="Level"
                        unit="%"
                        min={NOISE_SUPPRESSION_MIN_LEVEL}
                        max={NOISE_SUPPRESSION_MAX_LEVEL}
                        step={5}
                        value={suppression.level ?? NOISE_SUPPRESSION_MAX_LEVEL}
                        onValueChange={(level) =>
                            onChange({
                                noise_suppression_configuration: { ...suppression, level },
                            })
                        }
                        hint="Lower it if speech starts to sound thin. Default: 100"
                    />
                )}
            </Setting>

            <Advanced open={advancedTouched}>
                <Setting
                    title="Interruption"
                    blurb={startOption?.description ?? ""}
                    control={
                        <Select
                            value={startStrategy}
                            onValueChange={(v: TurnStartStrategy) => onChange({ turn_start_strategy: v })}
                        >
                            <SelectTrigger className="h-8 w-[150px] text-xs" aria-label="Interruption strategy">
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {TURN_START_STRATEGY_OPTIONS.map((o) => (
                                    <SelectItem key={o.value} value={o.value}>
                                        {o.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    }
                >
                    {startStrategy === "min_words" && (
                        <Slider
                            id="turn_start_min_words"
                            label="Words before interrupting"
                            unit=" words"
                            min={1}
                            max={10}
                            step={1}
                            value={config.turn_start_min_words}
                            onValueChange={(v) => onChange({ turn_start_min_words: v })}
                            hint={`Raise it so a cough or a "mhm" no longer cuts the agent off. Default: ${DEFAULT_TURN_START_MIN_WORDS}`}
                        />
                    )}
                    {startStrategy === "provisional_vad" && (
                        <Slider
                            id="provisional_vad_pause_secs"
                            label="Provisional pause"
                            unit="s"
                            min={0.1}
                            max={5}
                            step={0.1}
                            value={config.provisional_vad_pause_secs}
                            onValueChange={(v) => onChange({ provisional_vad_pause_secs: v })}
                            hint={`How long to pause the agent while the transcript confirms the caller spoke. Default: ${DEFAULT_PROVISIONAL_VAD_PAUSE_SECS}s`}
                        />
                    )}
                    <Slider
                        id="interruption_backoff_secs"
                        label="Pause after being interrupted"
                        unit="s"
                        min={0}
                        max={3}
                        step={0.1}
                        value={config.interruption_backoff_secs ?? 0}
                        onValueChange={(v) => onChange({ interruption_backoff_secs: v })}
                        hint="Before the agent speaks again after the caller cuts in. 0 turns it off."
                    />
                </Setting>
                <Setting
                    title="Keypad"
                    blurb="Let the caller type a number instead of saying it. Tell the prompt callers may type."
                    control={
                        <Switch
                            id="accept-keypad-input"
                            aria-label="Accept keypad input"
                            checked={config.accept_keypad_input ?? false}
                            onCheckedChange={(v) => onChange({ accept_keypad_input: v })}
                        />
                    }
                />
            </Advanced>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Brain
// ---------------------------------------------------------------------------

export function BrainPanel({ config, onChange, tuning, onTuning }: PanelProps) {
    const backchannel = config.backchannel_configuration ?? {
        enabled: false,
        delay_secs: BACKCHANNEL_DEFAULT_DELAY_SECS,
        phrases: [],
    };
    const [phrasesText, setPhrasesText] = useState((backchannel.phrases ?? []).join("\n"));
    const advancedTouched = tuning.max_tokens !== undefined || Boolean(config.context_compaction_enabled);

    return (
        <div>
            <Setting
                title="Temperature"
                blurb="How much the wording varies. Low sticks to the prompt and repeats itself; high is more creative and drifts more often."
            >
                <Slider
                    id="temperature"
                    label={tuning.temperature === undefined ? "Vendor default" : "Set"}
                    min={TEMPERATURE_RANGE.min}
                    max={TEMPERATURE_RANGE.max}
                    step={TEMPERATURE_RANGE.step}
                    value={tuning.temperature ?? 0.7}
                    onValueChange={(v) => onTuning({ temperature: v })}
                    hint="Precise on the left, creative on the right. 0.5 to 0.7 suits a phone agent that must not invent."
                />
            </Setting>

            <Setting
                title="Speak the way callers do"
                blurb={
                    config.speak_like_callers ?? true
                        ? "Mixes English into the local language the way people actually talk. Your own prompt still wins."
                        : "Off. Told to speak Hindi, the model uses the formal register nobody speaks."
                }
                control={
                    <Switch
                        id="speak-like-callers"
                        aria-label="Speak the way callers do"
                        checked={config.speak_like_callers ?? true}
                        onCheckedChange={(v) => onChange({ speak_like_callers: v })}
                    />
                }
            />

            <Setting
                title="Let the agent end the call"
                blurb={
                    config.agent_can_end_call
                        ? "The agent can hang up when there is nothing left to do — nobody on the line, the caller finished, or a wrong number."
                        : "Off. Only the caller's own goodbye ends a call, so an agent talking to an empty line keeps talking."
                }
                control={
                    <Switch
                        id="agent-can-end-call"
                        aria-label="Let the agent end the call"
                        checked={config.agent_can_end_call ?? false}
                        onCheckedChange={(v) => onChange({ agent_can_end_call: v })}
                    />
                }
            />

            <Setting
                title="Fillers while thinking"
                blurb="A short “hmm” or “one moment” when a reply is slow to start, so a lookup does not sound like a dropped line."
                control={
                    <Switch
                        id="backchannel-enabled"
                        aria-label="Fill silence while thinking"
                        checked={backchannel.enabled}
                        onCheckedChange={(v) =>
                            onChange({ backchannel_configuration: { ...backchannel, enabled: v } })
                        }
                    />
                }
            >
                {backchannel.enabled && (
                    <>
                        <Slider
                            id="backchannel-delay"
                            label="Speak after"
                            unit="s"
                            min={0.5}
                            max={5}
                            step={0.1}
                            value={backchannel.delay_secs ?? BACKCHANNEL_DEFAULT_DELAY_SECS}
                            onValueChange={(delay_secs) =>
                                onChange({ backchannel_configuration: { ...backchannel, delay_secs } })
                            }
                        />
                        <Textarea
                            id="backchannel-phrases"
                            rows={2}
                            value={phrasesText}
                            onChange={(e) => {
                                setPhrasesText(e.target.value);
                                onChange({
                                    backchannel_configuration: {
                                        ...backchannel,
                                        phrases: linesToList(e.target.value),
                                    },
                                });
                            }}
                            placeholder={"Hmm.\nOne moment."}
                            className="resize-none text-sm"
                        />
                        <p className="text-xs text-muted-foreground">
                            One per line, in the language the agent speaks. Empty means the English defaults.
                        </p>
                    </>
                )}
            </Setting>

            <Advanced open={advancedTouched}>
                <Setting
                    title="Max tokens"
                    blurb="Ceiling on one reply. Too low cuts a sentence off mid-way, which sounds like the agent hung up."
                >
                    <Input
                        id="max_tokens"
                        type="number"
                        min={MAX_TOKENS_RANGE.min}
                        max={MAX_TOKENS_RANGE.max}
                        placeholder="Vendor default"
                        value={tuning.max_tokens ?? ""}
                        onChange={(e) => {
                            const raw = e.target.value;
                            if (raw === "") {
                                onTuning({ max_tokens: undefined });
                                return;
                            }
                            const v = parseInt(raw, 10);
                            if (!Number.isNaN(v)) onTuning({ max_tokens: v });
                        }}
                        className="h-8 text-sm"
                    />
                </Setting>
                <Setting
                    title="Context compaction"
                    blurb="Summarise the conversation when moving between nodes. Ignored by speech-to-speech models."
                    control={
                        <Switch
                            id="context-compaction-enabled"
                            aria-label="Context compaction"
                            checked={config.context_compaction_enabled ?? false}
                            onCheckedChange={(v) => onChange({ context_compaction_enabled: v })}
                        />
                    }
                />
            </Advanced>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Voice
// ---------------------------------------------------------------------------

export function VoicePanel({ workflowId, config, onChange, tuning, onTuning }: PanelProps) {
    const ambient = config.ambient_noise_configuration;
    const lexicon = config.pronunciation_lexicon ?? [];
    const [uploading, setUploading] = useState(false);
    const [uploadError, setUploadError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const { playingId, toggle: togglePlayback } = useAudioPlayback();

    const uploadAmbient = async (file: File) => {
        if (file.size > MAX_AMBIENT_NOISE_FILE_SIZE) {
            setUploadError(`File too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum is 10MB.`);
            return;
        }
        setUploading(true);
        setUploadError(null);
        try {
            const res = await getAmbientNoiseUploadUrlApiV1WorkflowAmbientNoiseUploadUrlPost({
                body: {
                    workflow_id: workflowId,
                    filename: file.name,
                    mime_type: file.type || "audio/wav",
                    file_size: file.size,
                },
            });
            if (res.error || !res.data?.upload_url) throw new Error("Failed to get upload URL");
            const put = await fetch(res.data.upload_url, {
                method: "PUT",
                body: file,
                headers: { "Content-Type": file.type || "audio/wav" },
            });
            if (!put.ok) throw new Error("File upload failed");
            onChange({
                ambient_noise_configuration: {
                    ...ambient,
                    storage_key: res.data.storage_key,
                    storage_backend: res.data.storage_backend,
                    original_filename: file.name,
                },
            });
        } catch (err) {
            setUploadError(err instanceof Error ? err.message : "Upload failed");
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = "";
        }
    };

    const setRow = (index: number, field: keyof PronunciationEntry, value: string) =>
        onChange({
            pronunciation_lexicon: lexicon.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
        });

    return (
        <div>
            <Setting
                title="Speed"
                blurb="How fast the voice talks. A little above 1 suits a confident receptionist; below it, a patient one."
            >
                <Slider
                    id="speed"
                    label={tuning.speed === undefined ? "Vendor default" : "Set"}
                    unit="×"
                    min={SPEED_RANGE.min}
                    max={SPEED_RANGE.max}
                    step={SPEED_RANGE.step}
                    value={tuning.speed ?? 1.0}
                    onValueChange={(v) => onTuning({ speed: v })}
                />
            </Setting>

            <Setting
                title="Background sound"
                blurb={
                    ambient.enabled
                        ? "Room tone behind the agent, so silence does not sound like a dropped line."
                        : "Off. The caller hears the voice against silence."
                }
                control={
                    <Switch
                        id="ambient-noise-enabled"
                        aria-label="Background sound"
                        checked={ambient.enabled}
                        onCheckedChange={(v) =>
                            onChange({ ambient_noise_configuration: { ...ambient, enabled: v } })
                        }
                    />
                }
            >
                {ambient.enabled && (
                    <>
                        <Slider
                            id="ambient-volume"
                            label="Volume"
                            min={0}
                            max={1}
                            step={0.1}
                            value={ambient.volume}
                            onValueChange={(volume) =>
                                onChange({ ambient_noise_configuration: { ...ambient, volume } })
                            }
                        />
                        {ambient.storage_key ? (
                            <div className="flex items-center gap-2 rounded-md border bg-muted/10 p-2">
                                <code className="flex-1 truncate rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                                    {ambient.original_filename || "Custom audio"}
                                </code>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    className="h-6 w-6 shrink-0 p-0"
                                    aria-label="Play background sound"
                                    onClick={async () => {
                                        try {
                                            await togglePlayback(
                                                "ambient-noise",
                                                ambient.storage_key!,
                                                ambient.storage_backend,
                                            );
                                        } catch {
                                            setUploadError("Failed to play audio");
                                        }
                                    }}
                                >
                                    {playingId === "ambient-noise" ? (
                                        <Pause className="h-3.5 w-3.5" />
                                    ) : (
                                        <Play className="h-3.5 w-3.5" />
                                    )}
                                </Button>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="ghost"
                                    className="h-6 w-6 shrink-0 p-0"
                                    aria-label="Remove custom audio"
                                    onClick={() =>
                                        onChange({
                                            ambient_noise_configuration: {
                                                enabled: ambient.enabled,
                                                volume: ambient.volume,
                                            },
                                        })
                                    }
                                >
                                    <X className="h-3.5 w-3.5" />
                                </Button>
                            </div>
                        ) : (
                            <div>
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept="audio/*"
                                    onChange={(e) => {
                                        const file = e.target.files?.[0];
                                        if (file) void uploadAmbient(file);
                                    }}
                                    className="hidden"
                                />
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => fileInputRef.current?.click()}
                                    disabled={uploading}
                                >
                                    {uploading ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Upload className="mr-2 h-4 w-4" />
                                    )}
                                    {uploading ? "Uploading…" : "Upload your own (max 10MB)"}
                                </Button>
                                <p className="mt-1 text-xs italic text-muted-foreground">
                                    Using the default office ambience
                                </p>
                            </div>
                        )}
                        {uploadError && <p className="text-xs text-destructive">{uploadError}</p>}
                    </>
                )}
            </Setting>

            <Setting
                title="Pronunciation"
                blurb="Fix any word the voice says wrong: your business name, a doctor's name, a locality. Write it how it should sound."
            >
                {lexicon.length > 0 && (
                    <div className="space-y-2">
                        {lexicon.map((row, index) => (
                            <div key={index} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
                                <Input
                                    aria-label="Word"
                                    value={row.find}
                                    onChange={(e) => setRow(index, "find", e.target.value)}
                                    placeholder="Narayani"
                                    className="h-8 text-sm"
                                />
                                <Input
                                    aria-label="Say it as"
                                    value={row.say}
                                    onChange={(e) => setRow(index, "say", e.target.value)}
                                    placeholder="Naa-raa-ya-nee"
                                    className="h-8 text-sm"
                                />
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8"
                                    aria-label="Remove pronunciation"
                                    onClick={() =>
                                        onChange({
                                            pronunciation_lexicon: lexicon.filter((_, i) => i !== index),
                                        })
                                    }
                                >
                                    <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                            </div>
                        ))}
                    </div>
                )}
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                        onChange({ pronunciation_lexicon: [...lexicon, { find: "", say: "" }] })
                    }
                >
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    Add a word
                </Button>
            </Setting>
        </div>
    );
}
