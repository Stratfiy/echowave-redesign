"use client";

/**
 * The pencil on a model tile.
 *
 * One slot at a time — transcriber, brain, voice, or the speech-to-speech
 * model — from what Decibyl sells for that slot, each with what it adds to
 * a minute. The voice tile also plays samples, because nobody chooses a
 * voice by reading its name. Saving writes only this slot; the other tiles
 * are untouched, which is the whole point of editing them one by one.
 *
 * A model that is not on the list is what the per-slot editor and the
 * customer's own keys are for; the link at the bottom goes there.
 */

import { Check, Loader2, Pencil } from "lucide-react";
import { useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { type VoiceOption,VoicePicker } from "@/components/agent/VoicePicker";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { detailFromResult } from "@/lib/apiError";
import { formatCreditsLabel } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

export type SlotComponent = "stt" | "llm" | "tts" | "realtime";

export type CatalogueOption = {
    provider: string;
    model: string;
    label: string;
    paise_per_minute: number | null;
    approximate: boolean;
};

const TITLES: Record<SlotComponent, string> = {
    stt: "Transcriber",
    llm: "Brain",
    tts: "Voice",
    realtime: "Speech-to-speech",
};

export function ModelSlotEditor({
    workflowId,
    component,
    current,
    options,
    voices,
    currentVoice,
    onSaved,
}: {
    workflowId: number;
    component: SlotComponent;
    /** The vendor and model the tile shows now, so the list opens on it. */
    current: { provider: string; model: string };
    options: CatalogueOption[];
    /** Managed voices with samples; only the voice tile shows them. */
    voices?: VoiceOption[];
    currentVoice?: string;
    onSaved: () => Promise<void> | void;
}) {
    const [open, setOpen] = useState(false);
    const [choice, setChoice] = useState<CatalogueOption | null>(null);
    const [voice, setVoice] = useState(currentVoice ?? "");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!open) return;
        setChoice(
            options.find((o) => o.provider === current.provider && o.model === current.model) ??
                options[0] ??
                null,
        );
        setVoice(currentVoice ?? "");
        setError(null);
    }, [open, options, current.provider, current.model, currentVoice]);

    const save = async () => {
        if (!choice) return;
        setSaving(true);
        setError(null);
        const result = await client.put({
            url: `/api/v1/workflow/${workflowId}/model-slot`,
            body: {
                component,
                provider: choice.provider,
                model: choice.model,
                voice: component === "tts" && voice ? voice : undefined,
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not change this model."));
            return;
        }
        setOpen(false);
        await onSaved();
    };

    const dirty =
        choice !== null &&
        (choice.provider !== current.provider ||
            choice.model !== current.model ||
            (component === "tts" && voice !== (currentVoice ?? "")));

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0"
                    aria-label={`Change ${TITLES[component].toLowerCase()}`}
                >
                    <Pencil className="h-3.5 w-3.5" />
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[360px] p-0">
                <div className="border-b border-border px-4 py-3">
                    <p className="text-sm font-medium">{TITLES[component]}</p>
                    <p className="text-xs text-muted-foreground">
                        What it adds to a minute, on Decibyl&apos;s key.
                    </p>
                </div>
                <div className="max-h-72 overflow-y-auto p-2" role="radiogroup" aria-label={`${TITLES[component]} models`}>
                    {options.length === 0 && (
                        <p className="px-2 py-3 text-sm text-muted-foreground">
                            Nothing on offer for this slot yet.
                        </p>
                    )}
                    {options.map((option) => {
                        const active =
                            choice?.provider === option.provider && choice?.model === option.model;
                        return (
                            <button
                                key={`${option.provider}:${option.model}`}
                                type="button"
                                role="radio"
                                aria-checked={active}
                                onClick={() => setChoice(option)}
                                className={cn(
                                    "flex w-full items-center justify-between gap-3 rounded-lg px-2 py-2 text-left text-sm transition-colors",
                                    active ? "bg-[var(--accent-brand-soft)]" : "hover:bg-muted/50",
                                )}
                            >
                                <span className="min-w-0">
                                    <span className="block truncate font-medium">{option.label}</span>
                                    <span className="block truncate text-xs text-muted-foreground">
                                        {option.provider} · {option.model}
                                    </span>
                                </span>
                                <span className="flex shrink-0 items-center gap-2 text-xs tabular-nums text-muted-foreground">
                                    {option.paise_per_minute === null
                                        ? "—"
                                        : `${formatCreditsLabel(option.paise_per_minute)}/min${option.approximate ? " ≈" : ""}`}
                                    {active && <Check className="h-3.5 w-3.5 text-[var(--accent-brand)]" />}
                                </span>
                            </button>
                        );
                    })}
                </div>
                {component === "tts" && voices && voices.length > 0 && (
                    <div className="border-t border-border p-3">
                        <p className="mb-2 text-xs font-medium">Sound</p>
                        <VoicePicker voices={voices} selected={voice} onSelect={(id) => setVoice(id)} />
                    </div>
                )}
                {error && <p className="px-4 pb-2 text-xs text-destructive">{error}</p>}
                <div className="flex items-center justify-end gap-2 border-t border-border px-3 py-2">
                    <Button size="sm" onClick={() => void save()} disabled={!dirty || saving}>
                        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        Save
                    </Button>
                </div>
            </PopoverContent>
        </Popover>
    );
}

export default ModelSlotEditor;
