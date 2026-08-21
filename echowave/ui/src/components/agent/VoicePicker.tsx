/**
 * Choosing a voice by listening to it.
 *
 * Lifted out of the create wizard so the Models screen can use the same one.
 * Two screens rendering their own version of this drift within a release —
 * one gets the play button, the other keeps showing seven names — and the
 * customer meets whichever is worse.
 *
 * The component owns its audio element rather than taking one, because the
 * rule it enforces is a single player: seven `<audio>` tags would let two
 * voices talk over each other, which is a comic way to fail an audition.
 */

"use client";

import { Loader2, Play } from "lucide-react";
import { useRef, useState } from "react";

import { cn } from "@/lib/utils";

export type VoiceOption = {
    voice_id: string;
    name: string;
    gender: string | null;
    description: string | null;
    is_default: boolean;
    sample_url: string | null;
    sample_url_hi: string | null;
};

/**
 * Grouped, because seven Indian first names in one row is a list to read
 * rather than a choice to make. Gender is the only thing we can say about
 * these voices without having heard them — it is the vendor's own metadata,
 * not a guess from the name — so it is the only thing we sort by.
 */
const GROUPS = [
    ["female", "Female"],
    ["male", "Male"],
] as const;

export function VoicePicker({
    voices,
    selected,
    onSelect,
}: {
    voices: VoiceOption[];
    selected: string;
    /** Receives the voice id and the group heading, so a caller tracking
     *  gender for its own copy does not have to re-derive it. */
    onSelect: (voiceId: string, gender: string) => void;
}) {
    const playerRef = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState<string | null>(null);

    const play = (option: VoiceOption) => {
        // English first, Hindi as the fallback: a sample in a language the
        // listener does not speak still conveys the voice, and no sample at
        // all conveys nothing.
        const url = option.sample_url ?? option.sample_url_hi;
        if (!url) return;
        const player = playerRef.current ?? new Audio();
        playerRef.current = player;
        player.pause();
        player.src = url;
        setPlaying(option.voice_id);
        player.onended = () => setPlaying(null);
        player.onerror = () => setPlaying(null);
        void player.play().catch(() => setPlaying(null));
    };

    return (
        <div className="space-y-3">
            {GROUPS.map(([key, heading]) => {
                const group = voices.filter((v) => v.gender === key);
                if (!group.length) return null;
                return (
                    <div key={key}>
                        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            {heading}
                        </p>
                        <div className="flex flex-wrap gap-2">
                            {group.map((option) => {
                                const isSelected = selected === option.voice_id;
                                const hasSample =
                                    option.sample_url || option.sample_url_hi;
                                return (
                                    <span
                                        key={option.voice_id}
                                        className="inline-flex items-center"
                                    >
                                        <button
                                            type="button"
                                            aria-pressed={isSelected}
                                            onClick={() =>
                                                onSelect(option.voice_id, heading)
                                            }
                                            className={cn(
                                                "rounded-full border px-3 py-1.5 text-sm transition-colors",
                                                isSelected
                                                    ? "border-primary bg-primary text-primary-foreground"
                                                    : "border-border hover:border-foreground/30",
                                            )}
                                        >
                                            {option.description
                                                ? `${option.name} — ${option.description}`
                                                : option.is_default
                                                  ? `${option.name} · default`
                                                  : option.name}
                                        </button>
                                        {/* Only where there is something to play. A
                                            button that fails on click is worse than
                                            no button. */}
                                        {hasSample && (
                                            <button
                                                type="button"
                                                aria-label={`Hear ${option.name}`}
                                                onClick={() => play(option)}
                                                className="-ml-1 rounded-full p-1.5 text-muted-foreground transition-colors hover:text-foreground"
                                            >
                                                {playing === option.voice_id ? (
                                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                ) : (
                                                    <Play className="h-3.5 w-3.5" />
                                                )}
                                            </button>
                                        )}
                                    </span>
                                );
                            })}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
