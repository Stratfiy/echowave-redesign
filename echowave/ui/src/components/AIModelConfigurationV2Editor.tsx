"use client";

import { Info, KeyRound, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type {
    OrganizationAiModelConfigurationV2,
} from "@/client/types.gen";
import {
    type ProviderSchema,
    type ServiceConfigurationDefaults,
    ServiceConfigurationForm,
    type ServiceSegment,
} from "@/components/ServiceConfigurationForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VoiceSelectorModal } from "@/components/VoiceSelectorModal";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";

type ModelMode = "realtime" | "decibyl" | "byok";

// Sentinel language value for "Multilingual (Auto-detect)".
const MULTILINGUAL_LANGUAGE_CODE = "multi";

interface DecibylDefaults {
    voices: string[];
    allow_custom_input?: boolean;
    speeds: number[];
    speed_range?: {
        min: number;
        max: number;
        step?: number;
    };
    languages: string[];
    // Languages covered by the "multi" (Multilingual / Auto-detect) option.
    multilingual_languages?: string[];
    defaults: {
        voice: string;
        speed: number;
        language: string;
    };
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
}

interface DecibylFormState {
    api_key: string;
    voice: string;
    speed: number;
    language: string;
}

interface AIModelConfigurationV2EditorProps {
    defaults: ModelConfigurationDefaultsV2;
    configuration?: OrganizationAiModelConfigurationV2 | Record<string, unknown> | null;
    effectiveConfiguration?: Record<string, unknown> | null;
    onSave: (configuration: OrganizationAiModelConfigurationV2) => Promise<void>;
    submitLabel?: string;
}

function firstApiKey(value: unknown): string {
    if (Array.isArray(value)) return String(value[0] || "");
    return typeof value === "string" ? value : "";
}

function numberOrDefault(value: unknown, fallback: number): number {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

function isDecibylEffectiveConfig(config: Record<string, unknown> | null | undefined): boolean {
    if (!config || config.is_realtime) return false;
    const llm = asRecord(config.llm);
    const tts = asRecord(config.tts);
    const stt = asRecord(config.stt);
    return llm?.provider === "decibyl" && tts?.provider === "decibyl" && stt?.provider === "decibyl";
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

function byokConfigToLegacyShape(config: Record<string, unknown> | null): Record<string, unknown> | null {
    if (!config || config.mode !== "byok") return null;
    const byok = asRecord(config.byok);
    if (!byok) return null;

    if (byok.mode === "realtime") {
        const realtime = asRecord(byok.realtime);
        return {
            is_realtime: true,
            realtime: realtime?.realtime,
            llm: realtime?.llm,
            embeddings: realtime?.embeddings,
        };
    }

    const pipeline = asRecord(byok.pipeline);
    return {
        is_realtime: false,
        llm: pipeline?.llm,
        tts: pipeline?.tts,
        stt: pipeline?.stt,
        embeddings: pipeline?.embeddings,
    };
}

function effectiveConfigToLegacyShape(config: Record<string, unknown> | null): Record<string, unknown> | null {
    if (!config) return null;
    return {
        is_realtime: Boolean(config.is_realtime),
        llm: config.llm,
        tts: config.tts,
        stt: config.stt,
        realtime: config.realtime,
        embeddings: config.embeddings,
    };
}

function emptyByokInitialConfig(isRealtime: boolean): Record<string, unknown> {
    return {
        is_realtime: isRealtime,
    };
}

// The v2 editor surfaces realtime ("Speech to Speech") and pipeline (BYOK) as
// separate tabs, so each tab gets its own initial config. A tab is pre-filled
// only when the saved (or effective) configuration matches that tab's mode;
// otherwise it starts empty so the other tab's data does not leak across.
function getByokInitialConfig(
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
    wantRealtime: boolean,
): Record<string, unknown> {
    const matchesTab = (config: Record<string, unknown> | null) =>
        config ? Boolean(config.is_realtime) === wantRealtime : false;

    const byokConfiguration = byokConfigToLegacyShape(configuration);
    if (byokConfiguration) {
        return matchesTab(byokConfiguration) ? byokConfiguration : emptyByokInitialConfig(wantRealtime);
    }

    if (configuration?.mode === "decibyl" || isDecibylEffectiveConfig(effectiveConfiguration)) {
        return emptyByokInitialConfig(wantRealtime);
    }

    const effective = effectiveConfigToLegacyShape(effectiveConfiguration);
    return matchesTab(effective) ? (effective as Record<string, unknown>) : emptyByokInitialConfig(wantRealtime);
}

function buildDecibylState(
    defaults: ModelConfigurationDefaultsV2,
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): DecibylFormState {
    const fallback = defaults.decibyl.defaults;
    const configuredDecibyl = configuration?.mode === "decibyl" ? asRecord(configuration.decibyl) : null;
    if (configuredDecibyl) {
        return {
            api_key: String(configuredDecibyl.api_key || ""),
            voice: String(configuredDecibyl.voice || fallback.voice),
            speed: numberOrDefault(configuredDecibyl.speed, fallback.speed),
            language: String(configuredDecibyl.language || fallback.language),
        };
    }

    if (isDecibylEffectiveConfig(effectiveConfiguration)) {
        const llm = asRecord(effectiveConfiguration?.llm);
        const tts = asRecord(effectiveConfiguration?.tts);
        const stt = asRecord(effectiveConfiguration?.stt);
        return {
            api_key: firstApiKey(llm?.api_key || tts?.api_key || stt?.api_key),
            voice: String(tts?.voice || fallback.voice),
            speed: numberOrDefault(tts?.speed, fallback.speed),
            language: String(stt?.language || fallback.language),
        };
    }

    return {
        api_key: "",
        voice: fallback.voice,
        speed: fallback.speed,
        language: fallback.language,
    };
}

function preferredMode(
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): ModelMode {
    if (configuration?.mode === "decibyl") return "decibyl";
    if (configuration?.mode === "byok") {
        return asRecord(configuration.byok)?.mode === "realtime" ? "realtime" : "byok";
    }
    if (isDecibylEffectiveConfig(effectiveConfiguration)) return "decibyl";
    return Boolean(effectiveConfiguration?.is_realtime) ? "realtime" : "byok";
}

function hasRequiredApiKey(
    service: ServiceSegment,
    serviceConfiguration: Record<string, unknown>,
    defaults: ServiceConfigurationDefaults,
): boolean {
    const provider = serviceConfiguration.provider as string | undefined;
    if (!provider) return false;
    const providerSchema = service === "realtime"
        ? defaults.realtime?.[provider]
        : defaults[service as "llm" | "tts" | "stt" | "embeddings"]?.[provider];
    const requiresApiKey = providerSchema?.required?.includes("api_key") ?? false;
    if (!requiresApiKey) return true;

    const apiKey = serviceConfiguration.api_key;
    if (Array.isArray(apiKey)) {
        return apiKey.some((key) => typeof key === "string" && key.trim().length > 0);
    }
    return typeof apiKey === "string" && apiKey.trim().length > 0;
}

function requireByokService(
    config: Record<string, unknown>,
    service: ServiceSegment,
    defaults: ServiceConfigurationDefaults,
): Record<string, unknown> {
    const serviceConfiguration = asRecord(config[service]);
    if (
        !serviceConfiguration
        || !serviceConfiguration.provider
        || serviceConfiguration.provider === "decibyl"
        || !hasRequiredApiKey(service, serviceConfiguration, defaults)
    ) {
        throw new Error(`${service} configuration is required`);
    }
    return serviceConfiguration;
}

function optionalByokService(config: Record<string, unknown>, service: ServiceSegment): Record<string, unknown> | undefined {
    const serviceConfiguration = asRecord(config[service]);
    if (!serviceConfiguration?.provider || serviceConfiguration.provider === "decibyl") return undefined;
    return serviceConfiguration;
}

function ThirdPartyProviderNotice() {
    return (
        <div className="mt-4 flex gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 dark:text-amber-200">
            <Info className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
                <p className="font-medium">Third-party provider data notice</p>
                <p className="mt-1 leading-6">
                    Decibyl sends data required by the selected model service. This may include prompts,
                    transcripts, audio, generated text, tool data, and request metadata depending on the
                    provider and service type. Review the provider&apos;s data and retention policies before
                    using sensitive data.
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
    const defaultsForByok = useMemo(() => byokDefaults(defaults), [defaults]);
    const [mode, setMode] = useState<ModelMode>("decibyl");
    const [decibyl, setDecibyl] = useState<DecibylFormState>(() => ({
        api_key: "",
        voice: defaults.decibyl.defaults.voice,
        speed: defaults.decibyl.defaults.speed,
        language: defaults.decibyl.defaults.language,
    }));
    const [realtimeInitialConfig, setRealtimeInitialConfig] = useState<Record<string, unknown> | null>(null);
    const [pipelineInitialConfig, setPipelineInitialConfig] = useState<Record<string, unknown> | null>(null);
    const [isSavingDecibyl, setIsSavingDecibyl] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const allowCustomVoice = defaults.decibyl.allow_custom_input ?? false;
    const decibylSpeedRange = defaults.decibyl.speed_range ?? { min: 0.5, max: 2.0, step: 0.1 };
    const multilingualLanguageNames = useMemo(() => {
        const codes = defaults.decibyl.multilingual_languages ?? [];
        if (codes.length === 0) return null;
        return codes.map((code) => LANGUAGE_DISPLAY_NAMES[code] || code).join(", ");
    }, [defaults.decibyl.multilingual_languages]);

    useEffect(() => {
        const rawConfiguration = asRecord(configuration);
        const rawEffectiveConfiguration = asRecord(effectiveConfiguration);
        setMode(preferredMode(rawConfiguration, rawEffectiveConfiguration));
        const nextDecibyl = buildDecibylState(defaults, rawConfiguration, rawEffectiveConfiguration);
        setDecibyl(nextDecibyl);
        setRealtimeInitialConfig(getByokInitialConfig(rawConfiguration, rawEffectiveConfiguration, true));
        setPipelineInitialConfig(getByokInitialConfig(rawConfiguration, rawEffectiveConfiguration, false));
    }, [configuration, defaults, effectiveConfiguration, allowCustomVoice]);

    const saveDecibylConfiguration = async () => {
        setIsSavingDecibyl(true);
        setError(null);
        try {
            if (
                !Number.isFinite(decibyl.speed)
                || decibyl.speed < decibylSpeedRange.min
                || decibyl.speed > decibylSpeedRange.max
            ) {
                throw new Error(
                    `Decibyl speed must be between ${decibylSpeedRange.min} and ${decibylSpeedRange.max}.`,
                );
            }
            await onSave({
                version: 2,
                mode: "decibyl",
                decibyl: {
                    api_key: decibyl.api_key.trim(),
                    voice: decibyl.voice,
                    speed: decibyl.speed,
                    language: decibyl.language,
                },
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save configuration");
        } finally {
            setIsSavingDecibyl(false);
        }
    };

    const saveByokConfiguration = async (config: Record<string, unknown>) => {
        setError(null);
        const isRealtime = Boolean(config.is_realtime);
        const llm = requireByokService(config, "llm", defaultsForByok);
        const embeddings = optionalByokService(config, "embeddings");
        const body: OrganizationAiModelConfigurationV2 = {
            version: 2,
            mode: "byok",
            byok: isRealtime
                ? {
                    mode: "realtime",
                    realtime: {
                        realtime: requireByokService(config, "realtime", defaultsForByok) as never,
                        llm: llm as never,
                        ...(embeddings ? { embeddings: embeddings as never } : {}),
                    },
                }
                : {
                    mode: "pipeline",
                    pipeline: {
                        llm: llm as never,
                        tts: requireByokService(config, "tts", defaultsForByok) as never,
                        stt: requireByokService(config, "stt", defaultsForByok) as never,
                        ...(embeddings ? { embeddings: embeddings as never } : {}),
                    },
                },
        };

        await onSave(body);
    };

    return (
        <div className="space-y-6">
            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            <Tabs value={mode} onValueChange={(value) => setMode(value as ModelMode)} className="space-y-6">
                <TabsList className="grid w-full grid-cols-3">
                    <TabsTrigger value="realtime">Speech to Speech</TabsTrigger>
                    <TabsTrigger value="decibyl">Decibyl</TabsTrigger>
                    <TabsTrigger value="byok">BYOK</TabsTrigger>
                </TabsList>

                <TabsContent value="realtime" className="mt-0">
                    <p className="mb-4 text-sm text-muted-foreground">
                        A single speech-to-speech model handles the conversation in realtime (no separate transcriber or voice). An LLM is still required for variable extraction and QA.
                    </p>
                    <ServiceConfigurationForm
                        key={`realtime-${JSON.stringify(realtimeInitialConfig)}`}
                        mode="global"
                        forceRealtime
                        configurationDefaults={defaultsForByok}
                        initialConfig={realtimeInitialConfig}
                        submitLabel={submitLabel}
                        onSave={saveByokConfiguration}
                    />
                    <ThirdPartyProviderNotice />
                </TabsContent>

                <TabsContent value="decibyl" className="mt-0">
                    <p className="mb-4 text-sm text-muted-foreground">
                        Decibyl provides a managed transcriber, LLM, and voice pipeline. Select a voice and language while Decibyl manages the underlying model providers.{" "}
                        We offer custom pricing and a 15-second pulse with a monthly commitment.{" "}
                        <a
                            href="https://www.decibyl.com/contact"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline"
                        >
                            Contact us
                        </a>
                        .
                    </p>
                    <Card>
                        <CardContent className="pt-6">
                            <div className="grid gap-4 sm:grid-cols-2">
                                <div className="space-y-2 sm:col-span-2">
                                    <Label>Voice</Label>
                                    <VoiceSelectorModal
                                        provider="decibyl"
                                        value={decibyl.voice}
                                        onChange={(voice) => setDecibyl({ ...decibyl, voice })}
                                        allowManualInput={allowCustomVoice}
                                    />
                                </div>

                                <div className="space-y-2 sm:col-span-2">
                                    <Label>Language</Label>
                                    <Select value={decibyl.language} onValueChange={(language) => setDecibyl({ ...decibyl, language })}>
                                        <SelectTrigger className="w-full">
                                            <SelectValue placeholder="Select language" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {defaults.decibyl.languages.map((language) => (
                                                <SelectItem key={language} value={language}>
                                                    {LANGUAGE_DISPLAY_NAMES[language] || language}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    {decibyl.language === MULTILINGUAL_LANGUAGE_CODE && multilingualLanguageNames && (
                                        <p className="text-xs text-muted-foreground">
                                            Auto-detects {multilingualLanguageNames}.
                                        </p>
                                    )}
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="decibyl-speed">Speed</Label>
                                    <Input
                                        id="decibyl-speed"
                                        type="number"
                                        min={decibylSpeedRange.min}
                                        max={decibylSpeedRange.max}
                                        step={decibylSpeedRange.step ?? 0.1}
                                        value={decibyl.speed}
                                        onChange={(event) => {
                                            const speed = event.currentTarget.valueAsNumber;
                                            setDecibyl({
                                                ...decibyl,
                                                speed: Number.isFinite(speed) ? speed : defaults.decibyl.defaults.speed,
                                            });
                                        }}
                                    />
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="decibyl-api-key">API Key</Label>
                                    <div className="relative">
                                        <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                        <Input
                                            id="decibyl-api-key"
                                            className="pl-9"
                                            value={decibyl.api_key}
                                            onChange={(event) => setDecibyl({ ...decibyl, api_key: event.target.value })}
                                            placeholder="Enter API key"
                                        />
                                    </div>
                                </div>
                            </div>

                            <Button type="button" className="mt-6 w-full" onClick={saveDecibylConfiguration} disabled={isSavingDecibyl}>
                                <Save className="mr-2 h-4 w-4" />
                                {isSavingDecibyl ? "Saving..." : submitLabel}
                            </Button>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="byok" className="mt-0">
                    <p className="mb-4 text-sm text-muted-foreground">
                        Configure separate transcriber, LLM, and voice providers using your own API keys. An embeddings model can also be configured for knowledge retrieval.
                    </p>
                    <ServiceConfigurationForm
                        key={`byok-${JSON.stringify(pipelineInitialConfig)}`}
                        mode="global"
                        forceRealtime={false}
                        configurationDefaults={defaultsForByok}
                        initialConfig={pipelineInitialConfig}
                        submitLabel={submitLabel}
                        onSave={saveByokConfiguration}
                    />
                    <ThirdPartyProviderNotice />
                </TabsContent>
            </Tabs>
        </div>
    );
}
