"use client";

/**
 * Settings -> Personal -> Voice and language (screen 19; handoff 24).
 *
 * Language, a compatible voice with a cancellable preview, speed and
 * captions; the microphone's real browser state. Choosing a language filters
 * the voices and never silently swaps the chosen one: a voice that cannot
 * speak the new language is shown as such, and the last confirmed voice stays
 * until a new one is saved. Changes apply to new voice sessions -- a call in
 * progress keeps what it started with. Advanced speech models live under
 * Models.
 */

import { Mic, MicOff, Pause, Play } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { myVoicesApiV1MeSettingsVoicesGet } from "@/client/sdk.gen";
import type { VoiceCatalogue } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { SettingsSection } from "@/components/shell/SettingsSection";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { VoiceCallSettings } from "@/components/voice/VoiceCallSettings";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { cn } from "@/lib/utils";

import { LanguagePicker } from "../LanguagePicker";
import { Field, SettingsFormFrame } from "../SettingsForm";
import { type ProfileField, useProfileForm } from "../useProfileForm";

const FIELDS: readonly ProfileField[] = ["language", "auto_detect_language", "voice", "speaking_speed", "captions"];
const LABELS: Partial<Record<ProfileField, string>> = {
    language: "Language",
    auto_detect_language: "Detect my language",
    voice: "Voice",
    speaking_speed: "Speaking speed",
    captions: "Captions",
};

type Voices = VoiceCatalogue;
type MicState = "unknown" | "prompt" | "granted" | "denied" | "unsupported";

/** The microphone as the browser reports it -- never assumed. */
export function useMicrophone() {
    const [state, setState] = useState<MicState>("unknown");
    const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);

    const refresh = useCallback(async () => {
        if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
            setState("unsupported");
            return;
        }
        try {
            const status = await navigator.permissions?.query({ name: "microphone" as PermissionName });
            if (status) setState(status.state as MicState);
            else setState("prompt");
        } catch {
            setState("prompt");
        }
        try {
            const all = await navigator.mediaDevices.enumerateDevices();
            setDevices(all.filter((d) => d.kind === "audioinput" && d.deviceId));
        } catch {
            setDevices([]);
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const ask = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach((track) => track.stop());
        } catch {
            /* denied: refresh says so */
        }
        await refresh();
    }, [refresh]);

    return { state, devices, ask };
}

const MIC_KEY = "decibyl.microphone";

function Microphone() {
    const mic = useMicrophone();
    const [chosen, setChosen] = useState<string>(() => {
        try {
            return localStorage.getItem(MIC_KEY) ?? "";
        } catch {
            return "";
        }
    });
    const line: Record<MicState, string> = {
        unknown: "Checking…",
        prompt: "Not asked yet. Your browser will ask the first time you talk.",
        granted: "Allowed in this browser.",
        denied: "Blocked in this browser. Allow it from the address bar's site settings, then come back.",
        unsupported: "This browser cannot use a microphone here.",
    };
    return (
        <SettingsSection id="microphone" title="Microphone" description="On this device only: each browser asks for itself." scope="This device">
            <p className="flex items-center gap-2 text-sm" data-testid="mic-state" data-state={mic.state}>
                {mic.state === "denied" || mic.state === "unsupported" ? (
                    <MicOff aria-hidden className="h-4 w-4 text-destructive" />
                ) : (
                    <Mic aria-hidden className="h-4 w-4" />
                )}
                {line[mic.state]}
            </p>
            {mic.state === "prompt" && (
                <Button type="button" variant="outline" className="motion-m1 mt-3 min-h-11 md:min-h-9" onClick={() => void mic.ask()}>
                    Allow the microphone
                </Button>
            )}
            {mic.state === "granted" && mic.devices.length > 1 && (
                <Field id="mic-device" label="Which microphone">
                    <select
                        id="mic-device-input"
                        className="h-11 w-full rounded-[8px] border border-[#7B8491]/60 bg-background px-3 text-base md:h-9 md:text-sm"
                        value={chosen}
                        onChange={(event) => {
                            setChosen(event.target.value);
                            try {
                                localStorage.setItem(MIC_KEY, event.target.value);
                            } catch {
                                /* private mode */
                            }
                        }}
                    >
                        <option value="">The browser&apos;s default</option>
                        {mic.devices.map((device) => (
                            <option key={device.deviceId} value={device.deviceId}>
                                {device.label || "Microphone"}
                            </option>
                        ))}
                    </select>
                </Field>
            )}
        </SettingsSection>
    );
}

/** One preview at a time, with loading, playing and stop. */
function usePreview() {
    const audio = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState<string | null>(null);
    const [loading, setLoading] = useState<string | null>(null);
    const [failed, setFailed] = useState<string | null>(null);
    const stop = useCallback(() => {
        audio.current?.pause();
        audio.current = null;
        setPlaying(null);
        setLoading(null);
    }, []);
    useEffect(() => stop, [stop]);
    const play = useCallback(
        (id: string, url: string) => {
            stop();
            setFailed(null);
            const element = new Audio(url);
            audio.current = element;
            setLoading(id);
            element.oncanplay = () => setLoading((was) => (was === id ? null : was));
            element.onplaying = () => setPlaying(id);
            element.onended = () => setPlaying((was) => (was === id ? null : was));
            element.onerror = () => {
                setFailed(id);
                setLoading(null);
                setPlaying(null);
            };
            element.play().catch(() => {
                setFailed(id);
                setLoading(null);
            });
        },
        [stop],
    );
    return { playing, loading, failed, play, stop };
}

function VoiceForm() {
    const form = useProfileForm("settings-voice", FIELDS);
    const [voices, setVoices] = useState<Voices | null>(null);
    const [voicesFailed, setVoicesFailed] = useState(false);
    const latest = useRef(0);
    const preview = usePreview();
    const language = (form.draft.language as string | null) ?? null;
    const voice = (form.draft.voice as string | null) ?? null;
    const speed = (form.draft.speaking_speed as number | null) ?? 1;
    const captions = (form.draft.captions as boolean | null) ?? true;
    const autoDetect = (form.draft.auto_detect_language as boolean | null) ?? false;

    // Voices for the chosen language; only the latest answer is drawn, so a
    // quick change of language never shows an older list.
    const ready = form.phase === "ready";
    useEffect(() => {
        if (!ready) return;
        const mine = ++latest.current;
        setVoicesFailed(false);
        void (async () => {
            try {
                const result = await myVoicesApiV1MeSettingsVoicesGet({ query: language ? { language } : undefined });
                if (mine !== latest.current) return;
                if (result.error || !result.data) {
                    setVoicesFailed(true);
                    return;
                }
                setVoices(result.data);
            } catch {
                if (mine === latest.current) setVoicesFailed(true);
            }
        })();
    }, [ready, language]);

    const known = voices?.voices.some((v) => v.voice_id === voice);

    return (
        <SettingsFormFrame form={form} labels={LABELS}>
            <p className="rounded-[8px] bg-muted/50 px-3 py-2 text-sm" data-testid="new-session-note">
                Changes apply to your next voice session. A call in progress keeps the voice it started with.
            </p>
            <SettingsSection id="spoken-language" title="Language" description="The language you speak with Decibyl. Replies follow it too." scope="Just you">
                <div className="flex flex-col gap-4">
                    <LanguagePicker
                        id="spoken-language"
                        languages={form.stored?.languages ?? []}
                        value={language}
                        onChange={(tag) => form.set("language", tag)}
                        allowNone
                        noneLabel="Not set"
                        voiceNote
                    />
                    <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm">
                        <span>
                            Detect my language when I talk
                            <span className="block text-xs text-muted-foreground">Useful if you switch between languages.</span>
                        </span>
                        <Switch checked={autoDetect} onCheckedChange={(on) => form.set("auto_detect_language", on)} aria-label="Detect my language when I talk" />
                    </label>
                </div>
            </SettingsSection>

            <SettingsSection id="voice" title="Voice" description="Who Decibyl sounds like when it talks to you." scope="Just you">
                {voicesFailed && (
                    <p role="alert" className="text-sm text-destructive">
                        Could not load the voices. Your saved voice is unchanged.
                    </p>
                )}
                {!voices && !voicesFailed && <p className="text-sm text-muted-foreground">Loading voices…</p>}
                {voices && (
                    <div className="flex flex-col gap-3" data-testid="voice-list" data-readiness={voices.readiness}>
                        {voices.readiness === "needs_setup" && (
                            <p className="rounded-[8px] border border-[#705500]/40 px-3 py-2 text-sm text-[#705500] dark:text-amber-300">
                                Needs setup: {voices.readiness_reason}
                            </p>
                        )}
                        {voices.unavailable_reason && <p className="text-sm text-muted-foreground">{voices.unavailable_reason}</p>}
                        {voices.previews_available === false && (
                            <p className="text-sm text-muted-foreground">Voice samples could not be loaded just now. The list is right; previews are missing.</p>
                        )}
                        {voice && !known && voices.voices.length > 0 && (
                            <p className="text-sm text-[#705500] dark:text-amber-300">
                                Your voice “{voice}” cannot speak this language. Choose another, or keep it for other languages.
                            </p>
                        )}
                        <ul role="radiogroup" aria-label="Voices" className="grid gap-2 sm:grid-cols-2">
                            {voices.voices.map((option) => {
                                const selected = option.voice_id === voice;
                                const isPlaying = preview.playing === option.voice_id;
                                const isLoading = preview.loading === option.voice_id;
                                return (
                                    <li key={option.voice_id} className={cn("flex items-center gap-2 rounded-[8px] border p-1", selected ? "border-foreground" : "border-border")}>
                                        <button
                                            type="button"
                                            role="radio"
                                            aria-checked={selected}
                                            onClick={() => form.set("voice", option.voice_id)}
                                            className="motion-m1 flex min-h-11 min-w-0 flex-1 flex-col justify-center px-2 text-left text-sm"
                                        >
                                            <span>{option.name}</span>
                                            {option.gender && <span className="text-xs capitalize text-muted-foreground">{option.gender}</span>}
                                        </button>
                                        {option.sample_url ? (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                className="h-11 w-11 shrink-0 p-0"
                                                aria-label={isPlaying || isLoading ? `Stop ${option.name}` : `Hear ${option.name}`}
                                                onClick={() =>
                                                    isPlaying || isLoading ? preview.stop() : preview.play(option.voice_id, option.sample_url as string)
                                                }
                                            >
                                                {isPlaying || isLoading ? <Pause aria-hidden /> : <Play aria-hidden />}
                                            </Button>
                                        ) : (
                                            <span className="shrink-0 px-2 text-xs text-muted-foreground">No sample yet</span>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                        {preview.failed && <p className="text-sm text-destructive">That sample could not play. Your choice is unchanged.</p>}
                    </div>
                )}
            </SettingsSection>

            <SettingsSection id="speed" title="Speaking speed" description="How fast Decibyl talks." scope="Just you">
                <div className="flex items-center gap-3">
                    <input
                        type="range"
                        min={0.5}
                        max={2}
                        step={0.05}
                        value={speed}
                        aria-label="Speaking speed"
                        aria-valuetext={`${speed.toFixed(2)} times`}
                        onChange={(event) => form.set("speaking_speed", Number(event.target.value))}
                        className="h-11 min-w-0 flex-1 accent-[var(--foreground)]"
                    />
                    <output className="w-14 shrink-0 text-right text-sm tabular-nums">{speed.toFixed(2)}×</output>
                </div>
            </SettingsSection>

            <SettingsSection id="captions" title="Captions" description="Words on screen while Decibyl talks." scope="Just you">
                <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm">
                    <span>Show captions</span>
                    <Switch checked={captions} onCheckedChange={(on) => form.set("captions", on)} aria-label="Show captions" />
                </label>
            </SettingsSection>

            <Microphone />
        </SettingsFormFrame>
    );
}

export function VoiceSettings() {
    return (
        <UnsavedChangesProvider>
            <PageHeader title="Voice and language" description="How you talk with Decibyl. Speech models for agents are under Models." />
            <PageBody className="max-w-[640px]">
                <VoiceForm />
                {/* Stream voice: the Call and Appointment helper's policy, while on. */}
                <div className="mt-6">
                    <VoiceCallSettings />
                </div>
            </PageBody>
        </UnsavedChangesProvider>
    );
}

export default VoiceSettings;
