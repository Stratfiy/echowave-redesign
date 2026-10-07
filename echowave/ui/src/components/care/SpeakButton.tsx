"use client";

/**
 * "Say it instead": voice first, on every care box a person would otherwise
 * type into. The words land in the box to read and fix before anything is
 * sent -- the same dictation the Chat composer uses, never a live call. A
 * browser that cannot record says so in words; the button never pretends.
 */

import { Loader2, Mic, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDictation } from "@/lib/useDictation";
import { cn } from "@/lib/utils";

export function SpeakButton({ onText, className }: { onText: (text: string) => void; className?: string }) {
    const dictation = useDictation(onText);
    const busy = dictation.transcribing;
    return (
        <div className={cn("flex flex-col gap-1", className)}>
            <Button
                type="button"
                variant="outline"
                className="motion-m1 min-h-11 gap-2"
                aria-pressed={dictation.listening}
                disabled={busy}
                onClick={() => (dictation.listening ? dictation.stop() : void dictation.start())}
            >
                {busy ? (
                    <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                ) : dictation.listening ? (
                    <Square aria-hidden className="h-4 w-4" />
                ) : (
                    <Mic aria-hidden className="h-4 w-4" />
                )}
                {busy ? "Writing it down…" : dictation.listening ? "Stop, I'm done" : "Say it instead"}
            </Button>
            {dictation.error && (
                <p role="status" className="text-sm text-[#705500] dark:text-amber-300">
                    {dictation.error}
                </p>
            )}
        </div>
    );
}

export default SpeakButton;
