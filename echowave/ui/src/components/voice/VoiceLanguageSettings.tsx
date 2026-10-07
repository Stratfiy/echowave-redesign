"use client";

/**
 * Voice and language (screen 19, with stream `settings`).
 *
 * In the order the design asks: language, a voice that speaks it, a
 * cancellable preview, speed and captions -- one Save for all of them under
 * one revision, applied to the next voice session. A language change never
 * swaps the voice for another: an incompatible voice is cleared and the
 * screen asks for one (screen 19). The microphone's state in this browser is
 * shown as it is. Advanced transcriber and voice-model choices stay in
 * Settings, Models.
 *
 * Below it, for workspaces with the Call and Appointment helper switched on:
 * what calls may book, who places "call it for me" calls, and who a caller
 * is handed to -- each with its honest setup state.
 */

import { Loader2, Mic, MicOff, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    appointmentPolicyApiV1VoiceAppointmentsPolicyGet,
    getWorkflowsApiV1WorkflowFetchGet,
    myVoicePreferencesApiV1VoicePreferencesGet,
    saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut,
    saveMyVoicePreferencesApiV1VoicePreferencesPut,
    upcomingAppointmentsApiV1VoiceAppointmentsGet,
    voiceCatalogueApiV1VoiceCatalogueGet,
    voicePreviewApiV1VoicePreviewGet,
    voiceReadinessApiV1VoiceReadinessGet,
} from "@/client/sdk.gen";
import type {
    Appointment,
    AppointmentPolicy,
    ReadinessState,
    VoiceCatalogue,
    VoicePreferences,
} from "@/client/types.gen";
import { ConnectionRow, ErrorState, SaveBar, type SaveState, SettingsSection } from "@/components/shell";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

type Draft = Pick<VoicePreferences, "language" | "voice" | "voice_speed" | "captions">;

const FIELD = "min-h-11 w-full rounded-[8px] border border-[#7B8491] bg-background px-3 text-[16px] md:text-sm";

function same(a: Draft, b: Draft): boolean {
    return a.language === b.language && a.voice === b.voice && (a.voice_speed ?? 1) === (b.voice_speed ?? 1) && (a.captions ?? true) === (b.captions ?? true);
}

type MicState = "granted" | "denied" | "prompt" | "missing" | "unknown";

function useMicrophoneState(): MicState {
    const [state, setState] = useState<MicState>("unknown");
    useEffect(() => {
        if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
            setState("missing");
            return;
        }
        let cancelled = false;
        void (async () => {
            try {
                const status = await navigator.permissions?.query({ name: "microphone" as PermissionName });
                if (cancelled || !status) return;
                setState(status.state as MicState);
                status.onchange = () => setState(status.state as MicState);
            } catch {
                // Some browsers will not say; "unknown" is the honest answer.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);
    return state;
}

const MIC_LINE: Record<MicState, string> = {
    granted: "Allowed in this browser.",
    denied: "Blocked in this browser. Allow it in the site settings to talk with Decibyl.",
    prompt: "Your browser will ask the first time you press Talk.",
    missing: "This browser has no microphone access. You can still type or use another device.",
    unknown: "Your browser does not say. It will ask when you press Talk if it needs to.",
};

export function VoiceLanguageSettings() {
    const { user, loading: authLoading } = useAuth();
    const appointments = useFeature("call_appointment");
    const callForMe = useFeature("call_for_me");
    const [catalogue, setCatalogue] = useState<VoiceCatalogue | null>(null);
    const [confirmed, setConfirmed] = useState<VoicePreferences | null>(null);
    const [draft, setDraft] = useState<Draft | null>(null);
    const [save, setSave] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);
    const [stored, setStored] = useState<VoicePreferences | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const mic = useMicrophoneState();
    const fetched = useRef(false);

    const load = useCallback(async () => {
        setLoadError(null);
        const [cat, prefs] = await Promise.all([voiceCatalogueApiV1VoiceCatalogueGet(), myVoicePreferencesApiV1VoicePreferencesGet()]);
        if (cat.error || !cat.data || prefs.error || !prefs.data) {
            setLoadError(detailFromError(cat.error ?? prefs.error, "Voice settings could not load."));
            return;
        }
        setCatalogue(cat.data);
        setConfirmed(prefs.data);
        setDraft({ language: prefs.data.language, voice: prefs.data.voice, voice_speed: prefs.data.voice_speed, captions: prefs.data.captions });
        setSave("clean");
    }, []);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const language = catalogue?.languages.find((l) => l.code === draft?.language) ?? null;
    const dirty = !!draft && !!confirmed && !same(draft, confirmed);
    useEffect(() => {
        if (save === "saving" || save === "conflict" || save === "rejected") return;
        setSave(dirty ? "dirty" : save === "saved" ? "saved" : "clean");
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [dirty]);

    const change = (next: Partial<Draft>) => {
        setDraft((was) => (was ? { ...was, ...next } : was));
        if (save === "rejected" || save === "conflict" || save === "saved") setSave("dirty");
    };

    const chooseLanguage = (code: string) => {
        const target = catalogue?.languages.find((l) => l.code === code);
        const keeps = !!draft?.voice && !!target?.voices.some((v) => v.id === draft.voice);
        // Never substitute: an incompatible voice is cleared, and the person chooses.
        change({ language: code, voice: keeps ? draft?.voice ?? null : null });
    };

    const submit = async () => {
        if (!draft || !confirmed) return;
        setSave("saving");
        setMessage(null);
        const response = await saveMyVoicePreferencesApiV1VoicePreferencesPut({
            body: { revision: confirmed.revision, ...draft },
        });
        if (response.error || !response.data) {
            const detail = (response.error as { detail?: { stored?: VoicePreferences } } | undefined)?.detail;
            if (response.response?.status === 409 && detail?.stored) {
                setStored(detail.stored);
                setSave("conflict");
                return;
            }
            setMessage(detailFromError(response.error, "Your change was not saved. Try again."));
            setSave("rejected");
            return;
        }
        setConfirmed(response.data);
        setStored(null);
        setSave("saved");
    };

    const discard = () => {
        if (!confirmed) return;
        setDraft({ language: confirmed.language, voice: confirmed.voice, voice_speed: confirmed.voice_speed, captions: confirmed.captions });
        setStored(null);
        setSave("clean");
    };

    const useStored = () => {
        if (!stored) return;
        setConfirmed(stored);
        setDraft({ language: stored.language, voice: stored.voice, voice_speed: stored.voice_speed, captions: stored.captions });
        setStored(null);
        setSave("clean");
    };

    if (loadError) {
        return (
            <div className="mx-auto max-w-[640px] p-4 md:p-6">
                <ErrorState title="Voice settings could not load" description={loadError} onRetry={() => void load()} />
            </div>
        );
    }
    if (!catalogue || !draft || !confirmed) {
        return (
            <div className="mx-auto max-w-[640px] p-4 md:p-6" aria-busy="true">
                <div className="h-40 animate-pulse rounded-[8px] bg-muted motion-reduce:animate-none" />
            </div>
        );
    }

    return (
        <div className="mx-auto flex max-w-[640px] flex-col gap-4 p-4 md:p-6" data-testid="voice-settings">
            <h1 className="text-2xl font-medium">Voice and language</h1>
            <SettingsSection
                id="voice-language"
                title="Voice and language"
                scope="Just you"
                description="How Decibyl speaks to you when you press Talk. Changes apply to your next voice session."
            >
                <div className="flex flex-col gap-5">
                    <label className="flex flex-col gap-1.5 text-sm">
                        <span className="font-medium">Language</span>
                        <select
                            className={FIELD}
                            value={draft.language ?? ""}
                            onChange={(e) => chooseLanguage(e.target.value)}
                            data-testid="voice-language"
                        >
                            <option value="" disabled>
                                Choose a language
                            </option>
                            {catalogue.languages.map((l) => (
                                <option key={l.code} value={l.code}>
                                    {l.native === l.english ? l.english : `${l.native} — ${l.english}`}
                                    {l.spoken ? "" : " (text only)"}
                                </option>
                            ))}
                        </select>
                        <span className="text-muted-foreground">The language Decibyl listens for and answers in.</span>
                    </label>

                    <VoicePicker
                        voices={language?.voices ?? []}
                        languageCode={draft.language}
                        languageSpoken={!!language?.spoken}
                        value={draft.voice}
                        onChange={(voice) => change({ voice })}
                    />

                    <label className="flex flex-col gap-1.5 text-sm">
                        <span className="flex items-center justify-between font-medium">
                            Speed <span className="tabular-nums" data-testid="voice-speed-value">{(draft.voice_speed ?? 1).toFixed(2)}×</span>
                        </span>
                        <input
                            type="range"
                            className="h-11 w-full accent-foreground"
                            min={catalogue.speed_min}
                            max={catalogue.speed_max}
                            step={0.05}
                            value={draft.voice_speed ?? 1}
                            onChange={(e) => change({ voice_speed: Number(e.target.value) })}
                            aria-valuetext={`${(draft.voice_speed ?? 1).toFixed(2)} times`}
                        />
                        <span className="text-muted-foreground">How fast the voice speaks. 1.00× is normal.</span>
                    </label>

                    <label className="flex min-h-11 items-center justify-between gap-3 text-sm">
                        <span>
                            <span className="block font-medium">Captions</span>
                            <span className="text-muted-foreground">Show the words of both sides during a voice session.</span>
                        </span>
                        <input
                            type="checkbox"
                            className="h-5 w-5 accent-foreground"
                            checked={draft.captions ?? true}
                            onChange={(e) => change({ captions: e.target.checked })}
                        />
                    </label>

                    <div className="flex items-start gap-2 text-sm" data-testid="mic-state" data-state={mic}>
                        {mic === "denied" || mic === "missing" ? (
                            <MicOff aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-[#772322]" />
                        ) : (
                            <Mic aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                        )}
                        <span>
                            <span className="font-medium">Microphone: </span>
                            {MIC_LINE[mic]}
                        </span>
                    </div>
                </div>
                {save === "conflict" && stored && (
                    <div className="mt-4 rounded-[8px] border border-border p-3 text-sm" data-testid="voice-conflict">
                        <p>Saved elsewhere: {stored.language ?? "no language"}, {stored.voice ?? "no voice"}, speed {(stored.voice_speed ?? 1).toFixed(2)}×, captions {stored.captions === false ? "off" : "on"}.</p>
                        <p className="text-muted-foreground">Yours is still here. Keep yours to save over it, or use the saved version.</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                            <Button className="motion-m1 min-h-11" onClick={() => { setConfirmed(stored); setSave("dirty"); }}>
                                Keep mine
                            </Button>
                            <Button variant="outline" className="motion-m1 min-h-11" onClick={useStored}>
                                Use the saved version
                            </Button>
                        </div>
                    </div>
                )}
                <SaveBar state={save} onSave={() => void submit()} onDiscard={discard} message={message} className="mt-4 -mx-6" />
            </SettingsSection>

            {(appointments || callForMe) && <CallSettings appointments={appointments} />}
        </div>
    );
}

function VoicePicker({
    voices,
    languageCode,
    languageSpoken,
    value,
    onChange,
}: {
    voices: { id: string; label: string; model: string }[];
    languageCode: string | null | undefined;
    languageSpoken: boolean;
    value: string | null | undefined;
    onChange: (voice: string | null) => void;
}) {
    const [preview, setPreview] = useState<{ id: string; state: "loading" | "playing" } | null>(null);
    const [previewNote, setPreviewNote] = useState<string | null>(null);
    const audio = useRef<HTMLAudioElement | null>(null);
    const request = useRef(0);

    const stop = useCallback(() => {
        request.current += 1;
        audio.current?.pause();
        audio.current = null;
        setPreview(null);
    }, []);
    useEffect(() => stop, [stop]);
    useEffect(() => {
        stop();
        setPreviewNote(null);
    }, [languageCode, stop]);

    const play = async (id: string) => {
        stop();
        const mine = ++request.current;
        setPreview({ id, state: "loading" });
        setPreviewNote(null);
        const response = await voicePreviewApiV1VoicePreviewGet({ query: { voice: id, language: languageCode ?? "" } });
        // A later press wins: an old answer never plays over a new choice.
        if (mine !== request.current) return;
        if (response.error || !response.data || response.data.state !== "available" || !response.data.url) {
            setPreview(null);
            setPreviewNote(response.data?.reason ?? "Preview failed. The voice may still work in a session.");
            return;
        }
        const player = new Audio(response.data.url);
        audio.current = player;
        player.onended = () => mine === request.current && setPreview(null);
        try {
            await player.play();
            if (mine === request.current) setPreview({ id, state: "playing" });
        } catch {
            if (mine === request.current) {
                setPreview(null);
                setPreviewNote("Preview could not play in this browser.");
            }
        }
    };

    const grouped = useMemo(() => {
        const out = new Map<string, typeof voices>();
        for (const v of voices) out.set(v.model, [...(out.get(v.model) ?? []), v]);
        return [...out.entries()];
    }, [voices]);

    if (!languageCode) {
        return <p className="text-sm text-muted-foreground">Choose a language to see the voices that speak it.</p>;
    }
    if (!languageSpoken || voices.length === 0) {
        return (
            <p className="text-sm text-muted-foreground" data-testid="voice-unavailable">
                No voice speaks this language yet. Decibyl will answer in text and captions.
            </p>
        );
    }
    return (
        <fieldset className="flex flex-col gap-2 text-sm">
            <legend className="mb-1 font-medium">Voice</legend>
            {!value && <p className="text-[#705500]">Choose a voice that speaks this language.</p>}
            {grouped.map(([model, list]) => (
                <div key={model}>
                    <p className="mb-1 text-xs text-muted-foreground">{model}</p>
                    <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
                        {list.map((v) => {
                            const playing = preview?.id === v.id;
                            return (
                                <li key={v.id} className={cn("flex min-h-11 items-center gap-1 rounded-[8px] border px-2", value === v.id ? "border-foreground" : "border-border")}>
                                    <label className="flex min-h-11 flex-1 cursor-pointer items-center gap-2">
                                        <input type="radio" name="voice" value={v.id} checked={value === v.id} onChange={() => onChange(v.id)} />
                                        {v.label}
                                    </label>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="motion-m1 h-11 w-11"
                                        aria-label={playing ? `Stop ${v.label}` : `Preview ${v.label}`}
                                        onClick={() => (playing ? stop() : void play(v.id))}
                                    >
                                        {playing && preview?.state === "loading" ? (
                                            <Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                                        ) : playing ? (
                                            <Square aria-hidden className="h-4 w-4" />
                                        ) : (
                                            <Play aria-hidden className="h-4 w-4" />
                                        )}
                                    </Button>
                                </li>
                            );
                        })}
                    </ul>
                </div>
            ))}
            {previewNote && (
                <p role="status" className="text-muted-foreground" data-testid="preview-note">
                    {previewNote}
                </p>
            )}
        </fieldset>
    );
}

const BOOKING_LINE: Record<AppointmentPolicy["booking"], string> = {
    off: "Calls take details and the team calls back. Nothing is booked.",
    suggest: "Calls offer open times but never confirm one.",
    book: "Calls book an open time inside your hours, with the caller's details.",
};

function CallSettings({ appointments }: { appointments: boolean }) {
    const [readiness, setReadiness] = useState<ReadinessState | null>(null);
    const [policy, setPolicy] = useState<AppointmentPolicy | null>(null);
    const [draft, setDraft] = useState<AppointmentPolicy | null>(null);
    const [helpers, setHelpers] = useState<{ id: number; name: string }[]>([]);
    const [upcoming, setUpcoming] = useState<Appointment[] | null>(null);
    const [save, setSave] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const state = await voiceReadinessApiV1VoiceReadinessGet();
            setReadiness(state.data?.calls ?? null);
            if (!appointments) return;
            const [p, w, a] = await Promise.all([
                appointmentPolicyApiV1VoiceAppointmentsPolicyGet(),
                getWorkflowsApiV1WorkflowFetchGet(),
                upcomingAppointmentsApiV1VoiceAppointmentsGet(),
            ]);
            if (p.data) {
                setPolicy(p.data);
                setDraft(p.data);
            }
            setHelpers((w.data ?? []).map((wf) => ({ id: wf.id, name: wf.name })));
            setUpcoming(a.data ?? null);
        })();
    }, [appointments]);

    const change = (next: Partial<AppointmentPolicy>) => {
        setDraft((was) => (was ? { ...was, ...next } : was));
        setSave("dirty");
    };

    const submit = async () => {
        if (!draft || !policy) return;
        setSave("saving");
        const response = await saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut({
            body: {
                revision: policy.revision,
                booking: draft.booking,
                duration_minutes: draft.duration_minutes,
                services: draft.services,
                verification: draft.verification,
                escalate_to: draft.escalate_to || null,
                call_workflow_id: draft.call_workflow_id,
            },
        });
        if (response.error || !response.data) {
            const status = response.response?.status;
            setMessage(
                status === 403
                    ? "Only a workspace admin can change this."
                    : detailFromError(response.error, "Your change was not saved. Try again."),
            );
            setSave(status === 409 ? "conflict" : "rejected");
            return;
        }
        setPolicy(response.data);
        setDraft(response.data);
        setSave("saved");
    };

    return (
        <SettingsSection
            id="calls"
            title="Calls"
            scope="Workspace"
            description="What the Call and Appointment helper may do on calls, and who places calls you ask Decibyl to make."
        >
            <div className="flex flex-col gap-5 text-sm">
                {readiness && (
                    <ConnectionRow
                        name="Call it for me"
                        state={readiness.state}
                        reason={[readiness.reason, readiness.next_step].filter(Boolean).join(" ") || undefined}
                        detail={readiness.state === "available" ? "Every call is approved by you first and opens by saying it is Decibyl calling for you." : undefined}
                    />
                )}
                {appointments && draft && (
                    <>
                        <fieldset className="flex flex-col gap-2">
                            <legend className="mb-1 font-medium">Booking on calls</legend>
                            {(["off", "suggest", "book"] as const).map((mode) => (
                                <label key={mode} className="flex min-h-11 items-start gap-2">
                                    <input type="radio" name="booking" className="mt-1" checked={draft.booking === mode} onChange={() => change({ booking: mode })} />
                                    <span>
                                        <span className="block font-medium">{mode === "off" ? "Off" : mode === "suggest" ? "Offer times only" : "Book within hours"}</span>
                                        <span className="text-muted-foreground">{BOOKING_LINE[mode]}</span>
                                    </span>
                                </label>
                            ))}
                        </fieldset>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Appointment length (minutes)</span>
                            <input type="number" min={10} max={240} step={5} className={FIELD} value={draft.duration_minutes} onChange={(e) => change({ duration_minutes: Number(e.target.value) })} />
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Services</span>
                            <input
                                className={FIELD}
                                value={draft.services.join(", ")}
                                onChange={(e) => change({ services: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                                placeholder="Cleaning, Check-up"
                            />
                            <span className="text-muted-foreground">Separated by commas. Leave empty to accept any reason.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Who may book</span>
                            <select className={FIELD} value={draft.verification} onChange={(e) => change({ verification: e.target.value as AppointmentPolicy["verification"] })}>
                                <option value="details">Anyone who gives their name, number and reason</option>
                                <option value="known_caller">Only callers already in your contacts</option>
                            </select>
                            <span className="text-muted-foreground">Calls never read out an existing booking, whoever is calling.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Hand callers to</span>
                            <input className={FIELD} inputMode="tel" value={draft.escalate_to ?? ""} onChange={(e) => change({ escalate_to: e.target.value })} placeholder="+91 98765 43210" />
                            <span className="text-muted-foreground">When the helper is unsure. Empty: the team is told and calls back.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Helper that makes calls</span>
                            <select className={FIELD} value={draft.call_workflow_id ?? ""} onChange={(e) => change({ call_workflow_id: e.target.value ? Number(e.target.value) : null })}>
                                <option value="">None chosen</option>
                                {helpers.map((h) => (
                                    <option key={h.id} value={h.id}>
                                        {h.name}
                                    </option>
                                ))}
                            </select>
                            <span className="text-muted-foreground">Places &ldquo;call it for me&rdquo; calls and answers booking calls.</span>
                        </label>
                        <SaveBar state={save} onSave={() => void submit()} onDiscard={() => { setDraft(policy); setSave("clean"); }} message={message} className="-mx-6" />
                        <div>
                            <h3 className="mb-2 font-medium">Upcoming appointments</h3>
                            {upcoming === null ? (
                                <p className="text-muted-foreground">Could not load appointments.</p>
                            ) : upcoming.length === 0 ? (
                                <p className="text-muted-foreground">Nothing booked yet.</p>
                            ) : (
                                <ul className="divide-y divide-border">
                                    {upcoming.map((a) => (
                                        <li key={a.id} className="flex flex-wrap justify-between gap-2 py-2">
                                            <span>{new Date(a.starts_at).toLocaleString()}</span>
                                            <span className="text-muted-foreground">{[a.caller_name, a.service ?? a.reason].filter(Boolean).join(" · ")}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </>
                )}
            </div>
        </SettingsSection>
    );
}
