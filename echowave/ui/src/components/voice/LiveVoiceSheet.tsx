"use client";

/**
 * Live voice session (screen 05): a focused sheet on desktop, full screen on
 * a phone. One real input meter, a prominent state label, captions, and
 * controls that stay put -- End always visible and distinct. No animated
 * face, no fake waveform: the meter follows the microphone's actual level
 * and is shown only while it is capturing (motion M7).
 *
 * Approvals never happen by voice. When Decibyl proposes something, the
 * exact card waits in Chat; "Review it" minimises the sheet so the card is
 * in view, and the strip keeps the session one tap away.
 */

import { Captions, CaptionsOff, Mic, MicOff, Minimize2, PhoneOff, RotateCcw, Type } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ACTIVE, FINAL, LABEL, type VoiceState } from "@/lib/voice/sessionState";

/** Phases where the microphone is capturing, so the meter is real. */
const CAPTURING = new Set(["listening", "processing", "speaking"]);

export function LiveVoiceSheet({
    state,
    inputLevel,
    onEnd,
    onToggleMute,
    onMinimize,
    onRetry,
    onClose,
    captionsDefault = true,
}: {
    state: VoiceState;
    inputLevel: number;
    onEnd: () => void;
    onToggleMute: () => void;
    onMinimize: (minimized: boolean) => void;
    onRetry: () => void;
    onClose: () => void;
    captionsDefault?: boolean;
}) {
    const [captionsOn, setCaptionsOn] = useState(captionsDefault);
    useEffect(() => setCaptionsOn(captionsDefault), [captionsDefault]);
    const live = ACTIVE.has(state.phase);
    const final = FINAL.has(state.phase);
    const capturing = CAPTURING.has(state.phase) && !state.muted;
    const captionsEnd = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        captionsEnd.current?.scrollIntoView?.({ block: "end" });
    }, [state.captions]);
    const endRef = useRef<HTMLButtonElement | null>(null);
    useEffect(() => {
        if (!state.minimized && live) endRef.current?.focus();
        // Focus End once, when the sheet opens: the one control that must
        // always be reachable.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [state.minimized]);

    if (state.phase === "idle") return null;

    if (state.minimized) {
        return (
            <div
                className="fixed inset-x-4 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-50 flex items-center gap-2 rounded-[var(--radius)] border border-border bg-background p-2 shadow-lg md:inset-x-auto md:bottom-6 md:right-6 md:w-80"
                data-testid="voice-strip"
                role="region"
                aria-label="Live voice session"
            >
                <span className="flex min-w-0 flex-1 items-center gap-2 px-2 text-sm">
                    {state.muted ? <MicOff aria-hidden className="h-4 w-4 shrink-0" /> : <Mic aria-hidden className="h-4 w-4 shrink-0" />}
                    <span className="truncate">{LABEL[state.phase]}</span>
                </span>
                <Button variant="outline" className="motion-m1 min-h-11" onClick={() => onMinimize(false)}>
                    Open
                </Button>
                {live && (
                    <Button variant="destructive" className="motion-m1 min-h-11" onClick={onEnd} aria-label="End voice session">
                        End
                    </Button>
                )}
            </div>
        );
    }

    return (
        <section
            role="dialog"
            aria-modal="false"
            aria-labelledby="voice-sheet-title"
            data-testid="voice-sheet"
            data-phase={state.phase}
            className={cn(
                "motion-m3-enter fixed inset-0 z-50 flex flex-col bg-background",
                "md:inset-auto md:bottom-6 md:right-6 md:h-[min(640px,calc(100vh-3rem))] md:w-[440px] md:rounded-[var(--radius)] md:border md:border-border md:shadow-xl",
            )}
        >
            <header className="flex min-h-14 items-center justify-between gap-2 border-b border-border px-4 pt-[env(safe-area-inset-top)]">
                <h2 id="voice-sheet-title" className="text-base font-medium">
                    Talk with Decibyl
                </h2>
                <div className="flex items-center gap-1">
                    <Button
                        variant="ghost"
                        size="icon"
                        className="motion-m1 h-11 w-11"
                        aria-pressed={captionsOn}
                        aria-label={captionsOn ? "Hide captions" : "Show captions"}
                        onClick={() => setCaptionsOn((on) => !on)}
                    >
                        {captionsOn ? <Captions aria-hidden className="h-5 w-5" /> : <CaptionsOff aria-hidden className="h-5 w-5" />}
                    </Button>
                    {live && (
                        <Button
                            variant="ghost"
                            size="icon"
                            className="motion-m1 h-11 w-11"
                            aria-label="Minimise"
                            onClick={() => onMinimize(true)}
                        >
                            <Minimize2 aria-hidden className="h-5 w-5" />
                        </Button>
                    )}
                </div>
            </header>

            <div className="flex flex-col items-center gap-3 px-4 py-6 text-center">
                <p
                    className="text-2xl font-medium"
                    role="status"
                    aria-live="polite"
                    data-testid="voice-state-label"
                >
                    {LABEL[state.phase]}
                </p>
                {state.notice && (
                    <p className="max-w-sm text-sm text-muted-foreground" data-testid="voice-notice">
                        {state.notice}
                    </p>
                )}
                <p className="flex items-center gap-1.5 text-sm text-muted-foreground" data-testid="mic-indicator">
                    {live && !state.muted ? (
                        <>
                            <Mic aria-hidden className="h-4 w-4 text-[#075A39]" /> Microphone on
                        </>
                    ) : (
                        <>
                            <MicOff aria-hidden className="h-4 w-4" /> Microphone off
                        </>
                    )}
                </p>
                {capturing ? (
                    <div
                        className="h-2 w-48 overflow-hidden rounded-full bg-muted"
                        role="meter"
                        aria-label="Microphone level"
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(Math.min(1, inputLevel * 5) * 100)}
                        data-testid="input-meter"
                    >
                        <div
                            className="h-full rounded-full bg-foreground"
                            style={{ width: `${Math.round(Math.min(1, inputLevel * 5) * 100)}%` }}
                        />
                    </div>
                ) : (
                    <div className="h-2" aria-hidden />
                )}
            </div>

            {state.approvalWaiting && live && (
                <div className="mx-4 mb-3 flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius)] border border-border p-3 text-sm" data-testid="voice-approval">
                    <span>Decibyl put something on screen for your approval. Nothing happens until you tap it.</span>
                    <Button variant="outline" className="motion-m1 min-h-11" onClick={() => onMinimize(true)}>
                        Review it
                    </Button>
                </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto px-4" aria-label="Captions" role="log">
                {captionsOn &&
                    state.captions.map((caption) => (
                        <p
                            key={caption.id}
                            className={cn(
                                "mb-2 break-words text-[16px] leading-[1.6]",
                                caption.who === "you" ? "text-right text-muted-foreground" : "text-foreground",
                            )}
                        >
                            <span className="sr-only">{caption.who === "you" ? "You: " : "Decibyl: "}</span>
                            {caption.text}
                        </p>
                    ))}
                <div ref={captionsEnd} />
            </div>

            <footer className="flex flex-wrap items-center justify-center gap-3 border-t border-border px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
                {live && (
                    <>
                        <Button
                            variant="outline"
                            className="motion-m1 min-h-11 min-w-28"
                            aria-pressed={state.muted}
                            onClick={onToggleMute}
                            disabled={state.phase === "requesting_mic"}
                        >
                            {state.muted ? <MicOff aria-hidden className="mr-2 h-4 w-4" /> : <Mic aria-hidden className="mr-2 h-4 w-4" />}
                            {state.muted ? "Unmute" : "Mute"}
                        </Button>
                        <Button
                            ref={endRef}
                            variant="destructive"
                            className="motion-m1 min-h-11 min-w-28"
                            onClick={onEnd}
                            data-testid="voice-end"
                        >
                            <PhoneOff aria-hidden className="mr-2 h-4 w-4" /> End
                        </Button>
                    </>
                )}
                {final && (
                    <>
                        {state.phase !== "needs_setup" && state.phase !== "limit_reached" && (
                            <Button variant="outline" className="motion-m1 min-h-11" onClick={onRetry}>
                                <RotateCcw aria-hidden className="mr-2 h-4 w-4" /> Try again
                            </Button>
                        )}
                        <Button className="motion-m1 min-h-11" onClick={onClose} data-testid="voice-continue-text">
                            <Type aria-hidden className="mr-2 h-4 w-4" /> Continue in text
                        </Button>
                    </>
                )}
            </footer>
        </section>
    );
}
