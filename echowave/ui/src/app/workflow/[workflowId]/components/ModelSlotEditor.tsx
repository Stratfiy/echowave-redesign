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
 * It opens as a panel down the right edge rather than a popover under the
 * pencil. A popover is sized for a menu; this is a list of models with
 * prices and, on the voice tile, a row of play buttons, and it needs the
 * height. The tiles stay visible beside it, so the price on the card and
 * the price on the list can be compared without closing anything.
 *
 * Below the models, the slot's settings — the transcriber's languages and
 * turn-taking, the brain's temperature and fillers, the voice's speed and
 * background sound — so a slot is tuned where it is named. Two stores, one
 * Save: the slot's own knobs go with the model through the slot route, the
 * call configuration goes as a patch on the workflow, and either failing
 * keeps the panel open with the reason.
 */

import { Check, Loader2, Pencil } from "lucide-react";
import { useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import { type VoiceOption, VoicePicker } from "@/components/agent/VoicePicker";
import { Button } from "@/components/ui/button";
import {
    Sheet,
    SheetClose,
    SheetContent,
    SheetDescription,
    SheetFooter,
    SheetHeader,
    SheetTitle,
    SheetTrigger,
} from "@/components/ui/sheet";
import { detailFromResult } from "@/lib/apiError";
import { formatCreditsLabel } from "@/lib/billing/format";
import { cn } from "@/lib/utils";
import {
    resolveWorkflowConfigurations,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

import { BrainPanel, type SlotTuning, TranscriberPanel, VoicePanel } from "./SlotSettings";

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

/** One line under the title saying what this slot does to a call. */
const BLURBS: Record<SlotComponent, string> = {
    stt: "Turns what the caller says into text. Faster models cut the pause before the reply.",
    llm: "Decides what to say. Smarter models handle harder conversations and cost more a minute.",
    tts: "Speaks the reply. Pick the model, then the voice it speaks in.",
    realtime: "Listens, thinks and speaks as one model. No separate transcriber or voice.",
};

export function ModelSlotEditor({
    workflowId,
    component,
    current,
    options,
    voices,
    currentVoice,
    latencyMs,
    tuning,
    configurations,
    onSaveConfigurations,
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
    /** This agent's own measured reply time for the slot, when it has one. */
    latencyMs?: number | null;
    /** The slot's own knobs as stored, so the sliders open where the agent is. */
    tuning?: SlotTuning;
    /** The agent's call configuration; the panel edits the parts this slot owns. */
    configurations?: WorkflowConfigurations | null;
    onSaveConfigurations?: (patch: Partial<WorkflowConfigurations>) => Promise<void>;
    onSaved: () => Promise<void> | void;
}) {
    const [open, setOpen] = useState(false);
    const [choice, setChoice] = useState<CatalogueOption | null>(null);
    const [voice, setVoice] = useState(currentVoice ?? "");
    const [draftTuning, setDraftTuning] = useState<SlotTuning>(tuning ?? {});
    const [draft, setDraft] = useState<Partial<WorkflowConfigurations>>({});
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
        setDraftTuning(tuning ?? {});
        setDraft({});
        setError(null);
    }, [open, options, current.provider, current.model, currentVoice, tuning]);

    const slotDirty =
        choice !== null &&
        (choice.provider !== current.provider ||
            choice.model !== current.model ||
            (component === "tts" && voice !== (currentVoice ?? "")) ||
            JSON.stringify(draftTuning) !== JSON.stringify(tuning ?? {}));
    const configDirty = Object.keys(draft).length > 0;
    const dirty = slotDirty || configDirty;

    const save = async () => {
        if (!choice) return;
        setSaving(true);
        setError(null);
        // The slot first: a model the catalogue refuses should stop the
        // whole save, not land a half of it.
        if (slotDirty) {
            const result = await client.put({
                url: `/api/v1/workflow/${workflowId}/model-slot`,
                body: {
                    component,
                    provider: choice.provider,
                    model: choice.model,
                    voice: component === "tts" && voice ? voice : undefined,
                    ...draftTuning,
                },
            });
            if (result.error) {
                setSaving(false);
                setError(detailFromResult(result, "Could not change this model."));
                return;
            }
        }
        if (configDirty && onSaveConfigurations) {
            try {
                await onSaveConfigurations(draft);
            } catch (err) {
                setSaving(false);
                setError(err instanceof Error ? err.message : "Could not save these settings.");
                return;
            }
        }
        setSaving(false);
        setOpen(false);
        await onSaved();
    };

    // What the panel edits: the stored configuration with the draft on top,
    // so every control shows the value it would save.
    const panelConfig = configurations
        ? { ...resolveWorkflowConfigurations(configurations), ...draft }
        : null;
    const panelProps = panelConfig && {
        workflowId,
        config: panelConfig,
        onChange: (patch: Partial<WorkflowConfigurations>) =>
            setDraft((d) => ({ ...d, ...patch })),
        tuning: draftTuning,
        onTuning: (patch: SlotTuning) => setDraftTuning((t) => ({ ...t, ...patch })),
    };

    const title = TITLES[component];

    return (
        <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 shrink-0"
                    aria-label={`Change ${title.toLowerCase()}`}
                >
                    <Pencil className="h-3.5 w-3.5" />
                </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-md">
                <SheetHeader className="border-b border-border pr-12">
                    <SheetTitle>{title} settings</SheetTitle>
                    <SheetDescription>{BLURBS[component]}</SheetDescription>
                    <dl className="mt-1 flex gap-6 text-xs">
                        <div>
                            <dt className="uppercase tracking-wide text-muted-foreground">Now</dt>
                            <dd className="font-medium">{current.model || "—"}</dd>
                        </div>
                        <div>
                            <dt className="uppercase tracking-wide text-muted-foreground">Measured</dt>
                            <dd className="font-medium tabular-nums">
                                {latencyMs === null || latencyMs === undefined
                                    ? "—"
                                    : `${Math.round(latencyMs)}ms`}
                            </dd>
                        </div>
                    </dl>
                </SheetHeader>

                <div className="min-h-0 flex-1 overflow-y-auto">
                    <div className="p-2" role="radiogroup" aria-label={`${title} models`}>
                        <p className="px-2 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Model
                        </p>
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
                        <p className="px-2 pb-1 pt-2 text-[11px] text-muted-foreground">
                            What each adds to a minute, on Decibyl&apos;s key.
                        </p>
                    </div>
                    {component === "tts" && voices && voices.length > 0 && (
                        <div className="border-t border-border p-4">
                            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Voice
                            </p>
                            <VoicePicker voices={voices} selected={voice} onSelect={(id) => setVoice(id)} />
                        </div>
                    )}
                    {panelProps && component !== "realtime" && (
                        <div className="border-t border-border" data-testid="slot-settings">
                            <p className="px-4 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Settings
                            </p>
                            {component === "stt" && <TranscriberPanel {...panelProps} />}
                            {component === "llm" && <BrainPanel {...panelProps} />}
                            {component === "tts" && <VoicePanel {...panelProps} />}
                        </div>
                    )}
                </div>

                <SheetFooter className="mt-0 flex-row items-center justify-between gap-2 border-t border-border">
                    <span className="text-xs text-destructive">{error}</span>
                    <span className="flex items-center gap-2">
                        <SheetClose asChild>
                            <Button type="button" size="sm" variant="ghost">
                                Cancel
                            </Button>
                        </SheetClose>
                        <Button size="sm" onClick={() => void save()} disabled={!dirty || saving}>
                            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                            Save
                        </Button>
                    </span>
                </SheetFooter>
            </SheetContent>
        </Sheet>
    );
}

export default ModelSlotEditor;
