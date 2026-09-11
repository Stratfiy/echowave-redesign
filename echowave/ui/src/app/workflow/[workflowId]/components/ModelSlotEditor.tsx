"use client";

/**
 * The pencil on a model tile.
 *
 * One slot at a time — transcriber, brain, voice, or the speech-to-speech
 * model — from what Decibyl sells for that slot, each with what it adds to
 * a minute. Saving writes only this slot; the other tiles are untouched,
 * which is the whole point of editing them one by one.
 *
 * The model is chosen vendor first, then model, in two dropdowns. It was one
 * flat list, and that list is ten models long for the brain and eight for the
 * voice — on a phone it was a wall of names to scroll past, with the three
 * Sarvam models and the three ElevenLabs ones interleaved by price rather than
 * kept together. Nobody shops for a model that way: they know the vendor, or
 * they want to compare within one. Two dropdowns also hand a phone its own
 * native picker, which is a wheel rather than a page of rows.
 *
 * The voice slot then lists that model's own voices underneath, with a play
 * button on each. Changing the model changes the voices — Bulbul's speakers
 * are not ElevenLabs' — so the list is re-fetched for whatever is chosen,
 * rather than showing the voices of the model the agent used to have.
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

import { Loader2, Pencil } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
import {
    resolveWorkflowConfigurations,
    type WorkflowConfigurations,
} from "@/types/workflow-configurations";

import { BrainPanel, type SlotTuning, TranscriberPanel, VoicePanel } from "./SlotSettings";
import { vendorName } from "./vendors";

export type SlotComponent = "stt" | "llm" | "tts" | "realtime";

export type CatalogueOption = {
    provider: string;
    model: string;
    label: string;
    paise_per_minute: number | null;
    approximate: boolean;
};

/** Native selects, deliberately: on a phone this is the OS picker wheel
 *  rather than a scrolling page of rows, which is the whole complaint. */
const SELECT_CLASS =
    "h-10 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring";

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
    /** The chosen model's own voices. Null until asked for. */
    const [modelVoices, setModelVoices] = useState<VoiceOption[] | null>(null);
    const [loadingVoices, setLoadingVoices] = useState(false);
    const [draftTuning, setDraftTuning] = useState<SlotTuning>(tuning ?? {});
    const [draft, setDraft] = useState<Partial<WorkflowConfigurations>>({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const byProvider = useMemo(() => {
        const groups: { provider: string; models: CatalogueOption[] }[] = [];
        for (const option of options) {
            const group = groups.find((g) => g.provider === option.provider);
            if (group) group.models.push(option);
            else groups.push({ provider: option.provider, models: [option] });
        }
        return groups;
    }, [options]);

    useEffect(() => {
        if (!open) return;
        setChoice(
            options.find((o) => o.provider === current.provider && o.model === current.model) ??
                options[0] ??
                null,
        );
        setVoice(currentVoice ?? "");
        setModelVoices(null);
        setDraftTuning(tuning ?? {});
        setDraft({});
        setError(null);
    }, [open, options, current.provider, current.model, currentVoice, tuning]);

    // The voices belong to the model, not to the slot: Bulbul's speakers are
    // not ElevenLabs'. So they follow the dropdown rather than staying as the
    // agent's current model left them — otherwise somebody picks ElevenLabs,
    // plays a Sarvam name, and saves a voice that model has never heard of.
    useEffect(() => {
        if (!open || component !== "tts" || !choice) return;
        let live = true;
        setLoadingVoices(true);
        void (async () => {
            const result = await client.get({
                url: `/api/v1/user/configurations/voices/${choice.provider}`,
                query: { model: choice.model },
            });
            if (!live) return;
            setLoadingVoices(false);
            const fetched = (result.data as { voices?: VoiceOption[] } | undefined)?.voices;
            if (result.error || !Array.isArray(fetched)) {
                // The list we were handed is at least the right shape and was
                // right for the model the agent has. Better than an empty
                // picker if a vendor lookup is down.
                setModelVoices(null);
                return;
            }
            setModelVoices(
                fetched.map((v, index) => ({
                    ...v,
                    // Recorded on the first press now, so the list does not
                    // carry URLs; is_default is position, as the catalogue
                    // orders each model's speakers with the vendor's own first.
                    sample_url: v.sample_url ?? null,
                    sample_url_hi: v.sample_url_hi ?? null,
                    is_default: index === 0,
                })),
            );
        })();
        return () => {
            live = false;
        };
    }, [open, component, choice]);

    // What the picker shows: this model's voices once we have them, the ones
    // the tile handed us until then.
    const shownVoices = useMemo(() => modelVoices ?? voices ?? [], [modelVoices, voices]);

    // A voice from the model that was chosen before is not a voice this one
    // can speak in, and saving one would be refused — or worse, accepted.
    useEffect(() => {
        if (component !== "tts" || shownVoices.length === 0) return;
        if (shownVoices.some((option) => option.voice_id === voice)) return;
        setVoice(shownVoices[0].voice_id);
    }, [component, shownVoices, voice]);

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
                    <div className="space-y-3 p-4">
                        {options.length === 0 && (
                            <p className="text-sm text-muted-foreground">
                                Nothing on offer for this slot yet.
                            </p>
                        )}
                        {options.length > 0 && (
                            <>
                                <div className="space-y-1.5">
                                    <label
                                        htmlFor={`${component}-provider`}
                                        className="block text-xs font-medium uppercase tracking-wide text-muted-foreground"
                                    >
                                        Provider
                                    </label>
                                    <select
                                        id={`${component}-provider`}
                                        className={SELECT_CLASS}
                                        value={choice?.provider ?? ""}
                                        onChange={(event) => {
                                            // The first model of the vendor picked, so the
                                            // pair below is never left naming a model this
                                            // vendor does not have.
                                            const next = options.find(
                                                (option) => option.provider === event.target.value,
                                            );
                                            if (next) setChoice(next);
                                        }}
                                    >
                                        {byProvider.map(({ provider, models }) => (
                                            <option key={provider} value={provider}>
                                                {vendorName(provider)} ({models.length})
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="space-y-1.5">
                                    <label
                                        htmlFor={`${component}-model`}
                                        className="block text-xs font-medium uppercase tracking-wide text-muted-foreground"
                                    >
                                        Model
                                    </label>
                                    <select
                                        id={`${component}-model`}
                                        className={SELECT_CLASS}
                                        value={choice ? `${choice.provider}:${choice.model}` : ""}
                                        onChange={(event) => {
                                            const next = options.find(
                                                (option) =>
                                                    `${option.provider}:${option.model}` ===
                                                    event.target.value,
                                            );
                                            if (next) setChoice(next);
                                        }}
                                    >
                                        {(
                                            byProvider.find((g) => g.provider === choice?.provider)
                                                ?.models ?? []
                                        ).map((option) => (
                                            <option
                                                key={option.model}
                                                value={`${option.provider}:${option.model}`}
                                            >
                                                {option.label === option.model
                                                    ? option.model
                                                    : `${option.label} — ${option.model}`}
                                            </option>
                                        ))}
                                    </select>
                                    <p className="text-[11px] text-muted-foreground">
                                        {choice === null || choice.paise_per_minute === null
                                            ? "No price on file for this one yet."
                                            : `Adds ${formatCreditsLabel(choice.paise_per_minute)} a minute${choice.approximate ? " (about)" : ""}, on Decibyl's key.`}
                                    </p>
                                </div>
                            </>
                        )}
                    </div>
                    {component === "tts" && (
                        <div className="border-t border-border p-4">
                            <p className="mb-2 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                                Voice
                                {loadingVoices && <Loader2 className="h-3 w-3 animate-spin" />}
                            </p>
                            {shownVoices.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    {loadingVoices
                                        ? "Fetching this model's voices…"
                                        : "This model takes no named voice — it is directed by the text itself."}
                                </p>
                            ) : (
                                <VoicePicker
                                    voices={shownVoices}
                                    selected={voice}
                                    onSelect={(id) => setVoice(id)}
                                    provider={choice?.provider ?? "decibyl"}
                                    model={choice?.model ?? ""}
                                />
                            )}
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
