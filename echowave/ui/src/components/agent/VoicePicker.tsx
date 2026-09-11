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

import { client } from "@/client/client.gen";
import { cn } from "@/lib/utils";

export type VoiceOption = {
    voice_id: string;
    name: string;
    gender: string | null;
    description: string | null;
    is_default: boolean;
    sample_url: string | null;
    sample_url_hi: string | null;
    // Some providers (ElevenLabs) host their own preview and return it here
    // with sample_url null; without this the picker showed no play button for
    // any of them.
    preview_url?: string | null;
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

/** Anything whose vendor never said. OpenAI publishes no gender for its eleven
 *  voices, and ElevenLabs' account-fetched ones often carry none either — and
 *  a voice that matched no group above used to vanish from the picker without
 *  a trace, which for a whole provider reads as "this model has no voices". */
const UNGROUPED = "Other";

export function VoicePicker({
    voices,
    selected,
    onSelect,
    provider = "decibyl",
    model = "",
}: {
    voices: VoiceOption[];
    selected: string;
    /** Receives the voice id and the group heading, so a caller tracking
     *  gender for its own copy does not have to re-derive it. */
    onSelect: (voiceId: string, gender: string) => void;
    /** What will actually speak, so a voice nobody has heard yet can be
     *  recorded on the first press. Defaults to the managed tier. */
    provider?: string;
    model?: string;
}) {
    const playerRef = useRef<HTMLAudioElement | null>(null);
    const [playing, setPlaying] = useState<string | null>(null);
    // Voices we asked the server to record and it had nothing to give. Kept
    // so a second press does not repeat a request that already said no.
    const [silent, setSilent] = useState<Record<string, true>>({});

    const start = (voiceId: string, url: string) => {
        const player = playerRef.current ?? new Audio();
        playerRef.current = player;
        player.pause();
        player.src = url;
        setPlaying(voiceId);
        player.onended = () => setPlaying(null);
        player.onerror = () => setPlaying(null);
        void player.play().catch(() => setPlaying(null));
    };

    const play = async (option: VoiceOption) => {
        // English first, Hindi as the fallback: a sample in a language the
        // listener does not speak still conveys the voice, and no sample at
        // all conveys nothing.
        const cached = option.sample_url ?? option.sample_url_hi ?? option.preview_url;
        if (cached) {
            start(option.voice_id, cached);
            return;
        }
        // Nobody has ever pressed play on this voice. Ask the server to
        // record it now; it keeps the recording, so this happens once for
        // this voice and never again for anyone.
        setPlaying(option.voice_id);
        const result = await client.get({
            url: "/api/v1/agent-options/voice-sample",
            query: { voice_id: option.voice_id, provider, model },
        });
        const url = (result.data as { url?: string | null } | undefined)?.url;
        if (result.error || !url) {
            setPlaying(null);
            setSilent((s) => ({ ...s, [option.voice_id]: true }));
            return;
        }
        start(option.voice_id, url);
    };

    return (
        <div className="space-y-3">
            {[
                ...GROUPS.map(([key, heading]) => [heading, voices.filter((v) => v.gender === key)] as const),
                [
                    UNGROUPED,
                    voices.filter(
                        (v) => !GROUPS.some(([key]) => v.gender === key),
                    ),
                ] as const,
            ].map(([heading, group]) => {
                const key = heading;
                if (!group.length) return null;
                return (
                    <div key={key}>
                        <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            {heading}
                        </p>
                        <div className="flex flex-wrap gap-2">
                            {group.map((option) => {
                                const isSelected = selected === option.voice_id;
                                // A play button on every voice now, not only
                                // the ones already recorded: an unrecorded
                                // voice is one press away from being
                                // recorded. Only a voice the server has told
                                // us it cannot record loses its button.
                                const hasSample = !silent[option.voice_id];
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
                                        {/* Shown until the server says there is
                                            nothing to play. A button that fails
                                            on click is worse than no button, so
                                            one that comes back empty removes
                                            itself rather than failing twice. */}
                                        {hasSample && (
                                            <button
                                                type="button"
                                                aria-label={`Hear ${option.name}`}
                                                onClick={() => void play(option)}
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
