"use client";

/**
 * The input level while capturing (motion M7): it follows the microphone's
 * actual amplitude and stops when capture stops. No decorative pulse. With
 * reduced motion it is a static value and a label.
 */

import { useEffect, useState } from "react";

import { useReducedMotion } from "@/lib/motion";

export function useInputLevel(stream: MediaStream | null, active: boolean): number {
    const [level, setLevel] = useState(0);
    useEffect(() => {
        if (!stream || !active || typeof window === "undefined" || !("AudioContext" in window)) {
            setLevel(0);
            return;
        }
        let context: AudioContext | null = null;
        let timer: number | null = null;
        try {
            context = new AudioContext();
            const analyser = context.createAnalyser();
            analyser.fftSize = 256;
            context.createMediaStreamSource(stream).connect(analyser);
            const buffer = new Uint8Array(analyser.frequencyBinCount);
            timer = window.setInterval(() => {
                analyser.getByteTimeDomainData(buffer);
                let peak = 0;
                for (const sample of buffer) peak = Math.max(peak, Math.abs(sample - 128) / 128);
                setLevel(Math.min(1, peak * 1.6));
            }, 100);
        } catch {
            // No meter; capture is unaffected.
        }
        return () => {
            if (timer !== null) window.clearInterval(timer);
            void context?.close();
            setLevel(0);
        };
    }, [stream, active]);
    return level;
}

export function LevelMeter({ level, active }: { level: number; active: boolean }) {
    const reduced = useReducedMotion();
    const percent = Math.round(level * 100);
    if (reduced) {
        return (
            <p className="text-sm text-muted-foreground" data-testid="level-static">
                Input level: {active ? `${percent}%` : "not capturing"}
            </p>
        );
    }
    return (
        <div
            role="meter"
            aria-label="Microphone input level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={active ? percent : 0}
            className="h-2 w-full max-w-xs overflow-hidden rounded-full bg-muted"
        >
            <div
                className="h-full rounded-full bg-foreground"
                style={{ width: `${active ? Math.max(2, percent) : 0}%` }}
            />
        </div>
    );
}

export default LevelMeter;
