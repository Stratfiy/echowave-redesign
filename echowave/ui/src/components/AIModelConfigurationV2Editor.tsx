"use client";

import { Info, KeyRound, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { OrganizationAiModelConfigurationV2 } from "@/client/types.gen";
import { CostPerMinuteBar } from "@/components/CostPerMinuteBar";
import {
    type ProviderSchema,
    type ServiceConfigurationDefaults,
    ServiceConfigurationForm,
} from "@/components/ServiceConfigurationForm";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

// How the conversation is produced. This is the only top-level choice left on
// this screen, and it is deliberately not a choice about keys.
//
// It used to be one of three tabs — "Speech to Speech", "Decibyl", "BYOK" —
// which put an architecture beside two ways of paying for inference and
// presented all three as alternatives. Nothing on the screen said that picking
// Speech to Speech also committed you to bringing your own key, because that
// fact was in neither label.
//
// Whose key each model runs on is now a property of the slot: choose Decibyl as
// a provider and that slot is managed. See api/schemas/ai_model_configuration.py.
type Architecture = "pipeline" | "realtime";

const ARCHITECTURE_LABELS: Record<Architecture, string> = {
    pipeline: "Transcriber → Model → Voice",
    realtime: "Speech to Speech",
};

const ARCHITECTURE_BLURBS: Record<Architecture, string> = {
    pipeline:
        "Three models in a chain. More control over each stage, better Indic language support, and cheaper at most volumes.",
    realtime:
        "One model hears and speaks. Lower latency and more natural interruptions, but no separate transcriber or voice to tune.",
};

const MANAGED_PROVIDER = "decibyl";

interface DecibylDefaults {
    voices: string[];
    allow_custom_input?: boolean;
    speeds: number[];
    speed_range?: { min: number; max: number; step?: number };
    languages: string[];
    multilingual_languages?: string[];
    defaults: { voice: string; speed: number; language: string };
    // What the managed tiers resolve to today. Needed to price a managed stack:
    // the cost estimator is keyed by real provider and model, and "decibyl" is
    // neither.
    upstream?: Record<string, { provider: string; model: string }>;
}

export interface ModelConfigurationDefaultsV2 {
    decibyl: DecibylDefaults;
    byok: {
        pipeline: ServiceConfigurationDefaults;
        realtime: {
            realtime: Record<string, ProviderSchema>;
            llm: Record<string, ProviderSchema>;
            embeddings: Record<string, ProviderSchema>;
            default_providers: ServiceConfigurationDefaults["default_providers"];
        };
    };
    // Which vendors this account holds its own key for, by component. Used to
    // warn before a slot is pointed at a vendor we cannot authenticate to.
    byok_keys_held?: Record<string, string[]>;
}

interface AIModelConfigurationV2EditorProps {
    defaults: ModelConfigurationDefaultsV2;
    configuration?: OrganizationAiModelConfigurationV2 | Record<string, unknown> | null;
    effectiveConfiguration?: Record<string, unknown> | null;
    onSave: (configuration: OrganizationAiModelConfigurationV2) => Promise<void>;
    submitLabel?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

function byokDefaults(defaults: ModelConfigurationDefaultsV2): ServiceConfigurationDefaults {
    return {
        llm: defaults.byok.pipeline.llm,
        tts: defaults.byok.pipeline.tts,
        stt: defaults.byok.pipeline.stt,
        embeddings: defaults.byok.pipeline.embeddings,
        realtime: defaults.byok.realtime.realtime,
        default_providers: defaults.byok.pipeline.default_providers,
    };
}

/** The stored configuration flattened into the shape the form edits. */
function toFormConfig(
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): Record<string, unknown> {
    // A v2 "decibyl" configuration is every slot managed. Expanding it to
    // explicit managed slots is what lets the form show it as an ordinary stack
    // that happens to be all-Decibyl, rather than as a separate mode.
    if (configuration?.mode === "decibyl") {
        const managed = asRecord(configuration.decibyl) ?? {};
        return {
            is_realtime: false,
            llm: { provider: MANAGED_PROVIDER, model: "default" },
            stt: {
                provider: MANAGED_PROVIDER,
                model: "default",
                language: managed.language ?? "multi",
            },
            tts: {
                provider: MANAGED_PROVIDER,
                model: "default",
                voice: managed.voice ?? "default",
                speed: managed.speed ?? 1.0,
            },
        };
    }

    if (configuration?.mode === "byok") {
        const byok = asRecord(configuration.byok);
        if (byok?.mode === "realtime") {
            const realtime = asRecord(byok.realtime);
            return {
                is_realtime: true,
                realtime: realtime?.realtime,
                llm: realtime?.llm,
                embeddings: realtime?.embeddings,
            };
        }
        const pipeline = asRecord(byok?.pipeline);
        return {
            is_realtime: false,
            llm: pipeline?.llm,
            tts: pipeline?.tts,
            stt: pipeline?.stt,
            embeddings: pipeline?.embeddings,
        };
    }

    if (effectiveConfiguration) {
        return {
            is_realtime: Boolean(effectiveConfiguration.is_realtime),
            llm: effectiveConfiguration.llm,
            tts: effectiveConfiguration.tts,
            stt: effectiveConfiguration.stt,
            realtime: effectiveConfiguration.realtime,
            embeddings: effectiveConfiguration.embeddings,
        };
    }

    return { is_realtime: false };
}

function providerOf(config: Record<string, unknown>, slot: string): string {
    return String(asRecord(config[slot])?.provider ?? "");
}

function isManaged(config: Record<string, unknown>, slot: string): boolean {
    return providerOf(config, slot) === MANAGED_PROVIDER;
}

function requireSlot(
    config: Record<string, unknown>,
    slot: string,
): Record<string, unknown> {
    const value = asRecord(config[slot]);
    if (!value?.provider) {
        throw new Error(`Choose a provider for ${slot}.`);
    }
    return value;
}

function optionalSlot(
    config: Record<string, unknown>,
    slot: string,
): Record<string, unknown> | undefined {
    const value = asRecord(config[slot]);
    return value?.provider ? value : undefined;
}

function ThirdPartyProviderNotice() {
    return (
        <div className="mt-4 flex gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-200">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
                <p className="font-medium">Third-party provider data notice</p>
                <p className="mt-1 leading-6">
                    Decibyl sends data required by the selected model service. This may
                    include prompts, transcripts, audio, generated text, tool data, and
                    request metadata depending on the provider and service type. Review the
                    provider&apos;s data and retention policies before using sensitive data.
                </p>
            </div>
        </div>
    );
}

export function AIModelConfigurationV2Editor({
    defaults,
    configuration,
    effectiveConfiguration,
    onSave,
    submitLabel = "Save Configuration",
}: AIModelConfigurationV2EditorProps) {
    const defaultsForSlots = useMemo(() => byokDefaults(defaults), [defaults]);

    const [architecture, setArchitecture] = useState<Architecture>("pipeline");
    const [initialConfig, setInitialConfig] = useState<Record<string, unknown> | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const flattened = toFormConfig(
            asRecord(configuration),
            asRecord(effectiveConfiguration),
        );
        setArchitecture(flattened.is_realtime ? "realtime" : "pipeline");
        setInitialConfig(flattened);
    }, [configuration, effectiveConfiguration]);

    // Which slots the *saved* stack has on our keys, so the summary describes
    // reality rather than whatever is currently typed into the form.
    const savedSlots = useMemo(() => {
        const flattened = toFormConfig(
            asRecord(configuration),
            asRecord(effectiveConfiguration),
        );
        const slots = flattened.is_realtime
            ? ["realtime", "llm"]
            : ["stt", "llm", "tts"];
        return {
            managed: slots.filter((slot) => isManaged(flattened, slot)),
            byok: slots.filter(
                (slot) => providerOf(flattened, slot) && !isManaged(flattened, slot),
            ),
        };
    }, [configuration, effectiveConfiguration]);

    const save = async (config: Record<string, unknown>) => {
        setError(null);
        const isRealtime = Boolean(config.is_realtime);

        // Still written as v2 on the wire: the stored shape is upgraded to v3
        // on read, so nothing has to migrate on the way in. A slot naming
        // "decibyl" is no longer rejected, which is what makes a mixed stack
        // saveable at all.
        const llm = requireSlot(config, "llm");
        const embeddings = optionalSlot(config, "embeddings");
        const everySlotManaged = isRealtime
            ? isManaged(config, "realtime") && isManaged(config, "llm")
            : ["stt", "llm", "tts"].every((slot) => isManaged(config, slot));

        // An all-managed pipeline round-trips as mode "decibyl" so it keeps
        // loading on any deployment still reading the old shape, and so the
        // voice and language the customer chose survive in the place the
        // managed section stores them.
        if (everySlotManaged && !isRealtime) {
            const tts = asRecord(config.tts) ?? {};
            const stt = asRecord(config.stt) ?? {};
            await onSave({
                version: 2,
                mode: "decibyl",
                decibyl: {
                    api_key: "",
                    voice: String(tts.voice ?? "default"),
                    speed: Number(tts.speed ?? 1.0),
                    language: String(stt.language ?? "multi"),
                },
            });
            return;
        }

        await onSave({
            version: 2,
            mode: "byok",
            byok: isRealtime
                ? {
                      mode: "realtime",
                      realtime: {
                          realtime: requireSlot(config, "realtime") as never,
                          llm: llm as never,
                          ...(embeddings ? { embeddings: embeddings as never } : {}),
                      },
                  }
                : {
                      mode: "pipeline",
                      pipeline: {
                          llm: llm as never,
                          tts: requireSlot(config, "tts") as never,
                          stt: requireSlot(config, "stt") as never,
                          ...(embeddings ? { embeddings: embeddings as never } : {}),
                      },
                  },
        });
    };

    const upstream = defaults.decibyl.upstream;

    return (
        <div className="space-y-6">
            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            {/* One choice, and it is about shape rather than about money. */}
            <div className="space-y-3">
                <div>
                    <h2 className="text-sm font-medium">How the conversation runs</h2>
                    <p className="text-xs text-muted-foreground">
                        Pick the shape of the pipeline. Who pays for each model is a separate
                        choice, made per model below.
                    </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    {(["pipeline", "realtime"] as const).map((value) => {
                        const selected = architecture === value;
                        return (
                            <button
                                key={value}
                                type="button"
                                onClick={() => setArchitecture(value)}
                                className={`rounded-lg border p-4 text-left transition ${
                                    selected
                                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                                        : "border-border hover:border-primary/40"
                                }`}
                            >
                                <div className="flex items-center gap-2">
                                    {value === "realtime" && <Zap className="h-4 w-4" />}
                                    <span className="font-medium">
                                        {ARCHITECTURE_LABELS[value]}
                                    </span>
                                </div>
                                <p className="mt-1 text-xs text-muted-foreground">
                                    {ARCHITECTURE_BLURBS[value]}
                                </p>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* What the account is actually running, in the language of slots. */}
            <Card>
                <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-2 py-4 text-sm">
                    <span className="flex items-center gap-1.5 text-muted-foreground">
                        <KeyRound className="h-3.5 w-3.5" />
                        Currently running
                    </span>
                    {savedSlots.managed.length > 0 && (
                        <span className="flex items-center gap-1.5">
                            <Badge variant="secondary">Decibyl&apos;s keys</Badge>
                            {savedSlots.managed.join(", ")}
                        </span>
                    )}
                    {savedSlots.byok.length > 0 && (
                        <span className="flex items-center gap-1.5">
                            <Badge variant="outline">Your keys</Badge>
                            {savedSlots.byok.join(", ")}
                        </span>
                    )}
                    {savedSlots.managed.length === 0 && savedSlots.byok.length === 0 && (
                        <span className="text-muted-foreground">Nothing saved yet.</span>
                    )}
                </CardContent>
            </Card>

            {/* The same estimate for either kind of stack: units per minute
                measured from our own calls on that model, priced at the live
                rate card. Managed used to show no price at all, which made it
                look like the expensive option. */}
            {architecture === "pipeline" && (
                <CostPerMinuteBar
                    stack={{
                        stt_provider: upstream?.stt?.provider ?? null,
                        stt_model: upstream?.stt?.model ?? "",
                        llm_provider: upstream?.llm?.provider ?? null,
                        llm_model: upstream?.llm?.model ?? "",
                        tts_provider: upstream?.tts?.provider ?? null,
                        tts_model: upstream?.tts?.model ?? "",
                    }}
                />
            )}

            <div>
                <h2 className="text-sm font-medium">Models</h2>
                <p className="mb-3 text-xs text-muted-foreground">
                    Choose <span className="font-medium">Decibyl</span> for any model you
                    want us to provide and bill — no key needed. Choose a vendor to run it on
                    your own key from{" "}
                    <a href="/provider-keys" className="underline">
                        Provider Keys
                    </a>
                    . You can mix the two.
                </p>
                <ServiceConfigurationForm
                    key={`${architecture}-${JSON.stringify(initialConfig)}`}
                    mode="global"
                    forceRealtime={architecture === "realtime"}
                    configurationDefaults={defaultsForSlots}
                    initialConfig={initialConfig}
                    submitLabel={submitLabel}
                    onSave={save}
                />
                <ThirdPartyProviderNotice />
            </div>
        </div>
    );
}
