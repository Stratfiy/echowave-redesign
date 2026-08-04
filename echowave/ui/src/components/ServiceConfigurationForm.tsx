"use client";

import { AlertTriangle, ExternalLink, KeyRound, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";

import { getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet } from '@/client/sdk.gen';
import { CostPerMinuteBar } from "@/components/CostPerMinuteBar";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { VoiceSelector } from "@/components/VoiceSelector";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { useUserConfig } from "@/context/UserConfigContext";
import type { ModelOverrides } from "@/types/workflow-configurations";

export type ServiceSegment = "llm" | "tts" | "stt" | "embeddings" | "realtime";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    enum?: string[];
    examples?: string[];
    model_options?: Record<string, string[]>;
    allow_custom_input?: boolean;
    $ref?: string;
    description?: string;
    format?: string;
    multiline?: boolean;
    docs_url?: string;
}

export interface ProviderSchema {
    title?: string;
    description?: string;
    provider_docs_url?: string;
    properties: Record<string, SchemaProperty>;
    required?: string[];
    $defs?: Record<string, SchemaProperty>;
    [key: string]: unknown;
}

interface FormValues {
    [key: string]: string | number | boolean;
}

export interface ServiceConfigurationDefaults {
    llm: Record<string, ProviderSchema>;
    tts: Record<string, ProviderSchema>;
    stt: Record<string, ProviderSchema>;
    embeddings: Record<string, ProviderSchema>;
    realtime?: Record<string, ProviderSchema>;
    default_providers: Partial<Record<ServiceSegment, string>>;
}

const STANDARD_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
    { key: "embeddings", label: "Embedding" },
];

const REALTIME_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "realtime", label: "Realtime Model" },
    { key: "llm", label: "LLM" },
    { key: "embeddings", label: "Embedding" },
];

const OVERRIDE_STANDARD_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
];

const OVERRIDE_REALTIME_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "realtime", label: "Realtime Model" },
    { key: "llm", label: "LLM" },
];

// Display names for Sarvam voices
const VOICE_DISPLAY_NAMES: Record<string, string> = {
    "anushka": "Anushka (Female)",
    "manisha": "Manisha (Female)",
    "vidya": "Vidya (Female)",
    "arya": "Arya (Female)",
    "abhilash": "Abhilash (Male)",
    "karun": "Karun (Male)",
    "hitesh": "Hitesh (Male)",
};

export interface ServiceConfigurationFormProps {
    mode: 'global' | 'override';
    currentOverrides?: ModelOverrides;
    onSave: (config: Record<string, unknown>) => Promise<void>;
    /** Text for the submit button. Defaults to "Save Configuration". */
    submitLabel?: string;
    configurationDefaults?: ServiceConfigurationDefaults | null;
    initialConfig?: Record<string, unknown> | null;
    /**
     * When set, locks the realtime/pipeline mode to this value and hides the
     * in-form toggle. The model editor uses this because architecture is chosen
     * above the form, as its own question.
     * Leave undefined to keep the user-controllable toggle (legacy + overrides).
     */
    forceRealtime?: boolean;
    /**
     * What each managed tier resolves to, as `{ stt: { provider, model } }`.
     *
     * Needed to price a slot set to "decibyl": the cost estimator is keyed by
     * real provider and model, and "decibyl" is neither — so without this a
     * managed slot reports as unpriced and the whole stack reads "incomplete",
     * which is exactly wrong for the option we want people to pick.
     */
    managedUpstream?: Record<string, { provider: string; model: string }>;
    /**
     * Take keys from the organization's vault instead of rendering key inputs.
     *
     * When set, each slot gets an explicit "Decibyl provides it / My own key"
     * toggle and a line saying whether the key it depends on is stored. Off for
     * the legacy per-workflow override screen, which still carries its keys
     * inline.
     */
    keysFromVault?: boolean;
    /** Which vendors this account holds keys for, by component. */
    keysHeld?: Record<string, string[]>;
}

function getProviderDisplayName(
    provider: string | undefined,
    providerSchema: ProviderSchema | undefined,
): string | undefined {
    if (!provider) return provider;
    return providerSchema?.title || provider;
}

/** The provider value that means "Decibyl holds the key for this slot". */
const MANAGED = "decibyl";

/**
 * Whether the key this slot depends on is actually in the vault.
 *
 * Replaces the API-key input that used to sit here. The useful question on a
 * model screen is not "what is the key" — it is "will this slot authenticate",
 * and that is answerable without ever putting the secret on the page. Saying it
 * here rather than at dial time is the difference between a warning and a
 * wasted call.
 */
function VaultKeyStatus({
    service,
    provider,
    providerLabel,
    keysHeld,
}: {
    service: ServiceSegment;
    provider: string;
    providerLabel: string;
    keysHeld?: Record<string, string[]>;
}) {
    // Embeddings and realtime authenticate with the LLM credential: a vendor
    // issues one key for chat and embeddings alike, and a realtime model is a
    // language model that speaks. The backend resolves them the same way.
    const credentialComponent =
        service === "embeddings" || service === "realtime" ? "llm" : service;
    const held = keysHeld?.[credentialComponent] ?? [];
    const hasKey = held.includes(provider);

    if (hasKey) {
        return (
            <div className="flex items-start gap-2 rounded-md border border-input bg-muted/40 px-3 py-2.5 text-sm">
                <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="text-muted-foreground">
                    Runs on your stored {providerLabel} key.{" "}
                    <a href="/provider-keys" className="underline">Manage keys</a>
                </span>
            </div>
        );
    }

    return (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-900 dark:text-amber-200">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
                No {providerLabel} key stored, so this slot cannot authenticate and the
                call will fail.{" "}
                <a href="/provider-keys" className="underline">Add the key</a>, or switch
                this model to Decibyl.
            </span>
        </div>
    );
}

function getGlobalSummary(
    config: Record<string, unknown> | null | undefined,
    providerSchema: ProviderSchema | undefined,
): string {
    if (!config) return "Not configured";
    const provider = config.provider as string | undefined;
    const model = config.model as string | undefined;
    if (!provider) return "Not configured";
    const providerLabel = getProviderDisplayName(provider, providerSchema);
    return model ? `${providerLabel} / ${model}` : providerLabel || provider;
}

function getSchemaDropdownOptions(
    schema: SchemaProperty | undefined,
    modelValue?: string,
): string[] | undefined {
    let dropdownOptions = schema?.enum || schema?.examples;

    if (schema?.model_options && modelValue && schema.model_options[modelValue]) {
        dropdownOptions = schema.model_options[modelValue];
    }

    return dropdownOptions;
}

export function ServiceConfigurationForm({
    mode,
    currentOverrides,
    onSave,
    submitLabel,
    configurationDefaults,
    initialConfig,
    forceRealtime,
    managedUpstream,
    keysFromVault,
    keysHeld,
}: ServiceConfigurationFormProps) {
    // A slot set to "decibyl" is priced at whatever that tier resolves to
    // today. Passing "decibyl" straight through would report it as unpriced —
    // the estimator is keyed by real provider and model — so the managed
    // option would look like the one we cannot cost, which is the opposite of
    // true.
    const pricedSlot = (
        slot: "stt" | "llm" | "tts",
        provider: string | null | undefined,
        model: string,
    ) => {
        const upstream = provider === "decibyl" ? managedUpstream?.[slot] : undefined;
        return {
            [`${slot}_provider`]: upstream?.provider ?? provider ?? null,
            [`${slot}_model`]: upstream?.model ?? model ?? "",
        };
    };

    const [apiError, setApiError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [isRealtime, setIsRealtime] = useState(forceRealtime ?? false);
    const { userConfig } = useUserConfig();
    const [schemas, setSchemas] = useState<Record<ServiceSegment, Record<string, ProviderSchema>>>({
        llm: {},
        tts: {},
        stt: {},
        embeddings: {},
        realtime: {},
    });
    const [serviceProviders, setServiceProviders] = useState<Record<ServiceSegment, string>>({
        llm: "",
        tts: "",
        stt: "",
        embeddings: "",
        realtime: "",
    });
    const [apiKeys, setApiKeys] = useState<Record<ServiceSegment, string[]>>({
        llm: [""],
        tts: [""],
        stt: [""],
        embeddings: [""],
        realtime: [""],
    });
    const [isCustomInput, setIsCustomInput] = useState<Record<string, boolean>>({});

    // Override-specific state: which services have the override toggle enabled
    const [enabledOverrides, setEnabledOverrides] = useState<Record<string, boolean>>({
        llm: false,
        tts: false,
        stt: false,
        realtime: false,
    });

    const {
        register,
        handleSubmit,
        formState: { },
        reset,
        getValues,
        setValue,
        watch
    } = useForm();

    // Build effective config source: overlay overrides onto global config
    const configSource = useMemo(() => {
        const baseConfig = initialConfig ?? userConfig;
        if (mode === 'global' || !currentOverrides) return baseConfig;
        // Merge overrides onto global config for form initialization
        const merged = { ...baseConfig } as Record<string, unknown>;
        const overrideServices: (keyof ModelOverrides)[] = ["llm", "tts", "stt", "realtime"];
        for (const svc of overrideServices) {
            if (svc === "is_realtime") continue;
            const overrideVal = currentOverrides[svc];
            if (overrideVal && typeof overrideVal === "object") {
                const globalVal = (baseConfig as Record<string, unknown> | null)?.[svc] as Record<string, unknown> | undefined;
                merged[svc] = { ...globalVal, ...overrideVal };
            }
        }
        if (currentOverrides.is_realtime !== undefined) {
            merged.is_realtime = currentOverrides.is_realtime;
        }
        return merged as typeof userConfig;
    }, [mode, userConfig, currentOverrides, initialConfig]);

    useEffect(() => {
        const fetchConfigurations = async () => {
            let defaultsData = configurationDefaults;
            if (!defaultsData) {
                const response = await getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet();
                if (!response.data) {
                    console.error("Failed to fetch configurations");
                    return;
                }
                defaultsData = response.data as unknown as ServiceConfigurationDefaults;
            }

            const realtimeSchemas = (defaultsData.realtime || {}) as Record<string, ProviderSchema>;
            const pickDefaultProvider = (
                service: ServiceSegment,
                schemaMap: Record<string, ProviderSchema>,
            ) => {
                const preferred = defaultsData.default_providers?.[service];
                if (preferred && schemaMap[preferred]) return preferred;
                return Object.keys(schemaMap)[0] || "";
            };

            setSchemas({
                llm: defaultsData.llm,
                tts: defaultsData.tts,
                stt: defaultsData.stt,
                embeddings: defaultsData.embeddings,
                realtime: realtimeSchemas,
            });

            // Restore realtime toggle (skip when the parent locks the mode)
            const configData = configSource as Record<string, unknown> | null;
            if (forceRealtime === undefined && configData?.is_realtime) {
                setIsRealtime(true);
            }

            const defaultValues: Record<string, string | number | boolean> = {};
            const selectedProviders: Record<ServiceSegment, string> = {
                llm: pickDefaultProvider("llm", defaultsData.llm),
                tts: pickDefaultProvider("tts", defaultsData.tts),
                stt: pickDefaultProvider("stt", defaultsData.stt),
                embeddings: pickDefaultProvider("embeddings", defaultsData.embeddings),
                realtime: "",
            };

            const realtimeProviderKeys = Object.keys(realtimeSchemas);
            if (realtimeProviderKeys.length > 0) {
                selectedProviders.realtime = realtimeProviderKeys[0];
            }

            const loadedApiKeys: Record<ServiceSegment, string[]> = {
                llm: [""],
                tts: [""],
                stt: [""],
                embeddings: [""],
                realtime: [""],
            };

            const setServicePropertyValues = (service: ServiceSegment) => {
                const src = service === "realtime"
                    ? (configSource as Record<string, unknown> | null)?.realtime as Record<string, unknown> | undefined
                    : (configSource as Record<string, unknown> | null)?.[service] as Record<string, unknown> | undefined;

                const schemaSource = service === "realtime"
                    ? realtimeSchemas
                    : defaultsData[service as "llm" | "tts" | "stt" | "embeddings"] as Record<string, ProviderSchema> | undefined;

                if (src?.provider) {
                    Object.entries(src).forEach(([field, value]) => {
                        if (field === "api_key") {
                            if (mode === 'override') {
                                // In override mode, only load API keys from the override itself
                                const overrideVal = currentOverrides?.[service as keyof ModelOverrides];
                                const overrideApiKey = overrideVal && typeof overrideVal === "object"
                                    ? (overrideVal as Record<string, unknown>).api_key
                                    : undefined;
                                if (overrideApiKey) {
                                    loadedApiKeys[service] = Array.isArray(overrideApiKey)
                                        ? overrideApiKey as string[]
                                        : [overrideApiKey as string];
                                } else {
                                    loadedApiKeys[service] = [""];
                                }
                            } else {
                                if (Array.isArray(value)) {
                                    loadedApiKeys[service] = (value as string[]).length > 0 ? value as string[] : [""];
                                } else {
                                    loadedApiKeys[service] = value ? [value as string] : [""];
                                }
                            }
                        } else if (field !== "provider") {
                            defaultValues[`${service}_${field}`] = value as string | number | boolean;
                        }
                    });
                    selectedProviders[service] = src.provider as string;
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            const key = `${service}_${field}`;
                            if (field !== "provider" && field !== "api_key" && schema.default !== undefined && !(key in defaultValues)) {
                                defaultValues[key] = schema.default;
                            }
                        });
                    }
                } else {
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            if (field !== "provider" && schema.default !== undefined) {
                                defaultValues[`${service}_${field}`] = schema.default;
                            }
                        });
                    }
                }
            };

            setServicePropertyValues("llm");
            setServicePropertyValues("tts");
            setServicePropertyValues("stt");
            setServicePropertyValues("embeddings");
            setServicePropertyValues("realtime");

            // Detect custom inputs
            const detectedCustomInput: Record<string, boolean> = {};
            const allSchemas = { ...defaultsData, realtime: realtimeSchemas } as unknown as Record<string, Record<string, ProviderSchema>>;
            (["llm", "tts", "stt", "embeddings", "realtime"] as ServiceSegment[]).forEach(service => {
                const provider = selectedProviders[service];
                const providerSchema = allSchemas[service]?.[provider];
                if (!providerSchema) return;

                const src = service === "realtime"
                    ? (configSource as Record<string, unknown> | null)?.realtime as Record<string, unknown> | undefined
                    : (configSource as Record<string, unknown> | null)?.[service] as Record<string, unknown> | undefined;

                Object.entries(providerSchema.properties).forEach(([field, schema]) => {
                    const actualSchema = (schema as SchemaProperty).$ref && providerSchema.$defs
                        ? providerSchema.$defs[(schema as SchemaProperty).$ref!.split('/').pop() || '']
                        : schema as SchemaProperty;

                    if (!actualSchema?.allow_custom_input) return;

                    const savedValue = src?.[field] as string | undefined;
                    const modelValue = src?.model as string | undefined;
                    const dropdownOptions = getSchemaDropdownOptions(actualSchema, modelValue);
                    if (savedValue && dropdownOptions && !dropdownOptions.includes(savedValue)) {
                        detectedCustomInput[`${service}_${field}`] = true;
                    }
                });
            });

            // Initialize override toggles
            if (mode === 'override') {
                setEnabledOverrides({
                    llm: !!currentOverrides?.llm,
                    tts: !!currentOverrides?.tts,
                    stt: !!currentOverrides?.stt,
                    realtime: !!currentOverrides?.realtime,
                });
            }

            reset(defaultValues);
            setApiKeys(loadedApiKeys);
            setServiceProviders(selectedProviders);
            setIsCustomInput(detectedCustomInput);
        };
        fetchConfigurations();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reset, configSource, configurationDefaults]);

    // Reset voice when TTS model changes if the provider has model-dependent voice options
    const ttsModel = watch("tts_model");
    useEffect(() => {
        const voiceSchema = schemas?.tts?.[serviceProviders.tts]?.properties?.voice;
        const modelOptions = voiceSchema?.model_options;
        if (!modelOptions || !ttsModel) return;

        const validVoices = modelOptions[ttsModel as string];
        const currentVoice = getValues("tts_voice") as string;
        const isCustomVoice = !!isCustomInput.tts_voice;
        if (validVoices && currentVoice && !validVoices.includes(currentVoice) && !isCustomVoice) {
            setValue("tts_voice", validVoices[0], { shouldDirty: true });
        }
    }, [ttsModel, serviceProviders.tts, setValue, getValues, schemas, isCustomInput.tts_voice]);

    // Reset language when STT model changes if the provider has model-dependent language options
    const sttModel = watch("stt_model");
    useEffect(() => {
        const languageSchema = schemas?.stt?.[serviceProviders.stt]?.properties?.language;
        const modelOptions = languageSchema?.model_options;
        if (!modelOptions || !sttModel) return;

        const validLanguages = modelOptions[sttModel as string];
        const currentLanguage = getValues("stt_language") as string;
        if (validLanguages && currentLanguage && !validLanguages.includes(currentLanguage)) {
            setValue("stt_language", validLanguages[0], { shouldDirty: true });
        }
    }, [sttModel, serviceProviders.stt, setValue, getValues, schemas]);

    const handleProviderChange = (service: ServiceSegment, providerName: string) => {
        if (!providerName) return;

        const currentValues = getValues();
        const preservedValues: Record<string, string | number | boolean> = {};

        Object.keys(currentValues).forEach(key => {
            if (!key.startsWith(`${service}_`)) {
                preservedValues[key] = currentValues[key];
            }
        });

        if (schemas?.[service]?.[providerName]) {
            const providerSchema = schemas[service][providerName];
            Object.entries(providerSchema.properties).forEach(([field, schema]: [string, SchemaProperty]) => {
                if (field !== "provider" && schema.default !== undefined) {
                    preservedValues[`${service}_${field}`] = schema.default;
                }
            });
        }

        preservedValues[`${service}_provider`] = providerName;
        reset(preservedValues);
        setServiceProviders(prev => ({ ...prev, [service]: providerName }));
        setApiKeys(prev => ({ ...prev, [service]: [""] }));

        setIsCustomInput(prev => {
            const next = { ...prev };
            Object.keys(next).forEach(key => {
                if (key.startsWith(`${service}_`)) delete next[key];
            });
            return next;
        });
    };

    const buildServiceConfig = (service: ServiceSegment, data: FormValues) => {
        const config: Record<string, string | number | string[]> = {
            provider: serviceProviders[service],
        };
        // In vault mode the key is deliberately absent from the saved
        // configuration: byok_resolution looks it up at dial time, and an
        // inline key would win over the vault and quietly pin this slot to
        // whatever was pasted here once — surviving every later rotation.
        const keys = keysFromVault
            ? []
            : apiKeys[service].map(k => k.trim()).filter(k => k.length > 0);
        if (keys.length > 0) {
            config.api_key = mode === 'override' ? keys[0] : keys;
        } else if (keysFromVault) {
            // An explicit empty key, not an omitted one. The field stays
            // required on the backend so a configuration that genuinely forgot
            // a key still fails; sending "" says the key lives in the vault and
            // byok_resolution will supply it at dial time. Sending a real key
            // here would beat the vault at resolution and pin this slot to
            // whatever was pasted once, surviving every later rotation.
            config.api_key = "";
        }
        Object.entries(data).forEach(([property, value]) => {
            if (!property.startsWith(`${service}_`)) return;
            const field = property.slice(service.length + 1);
            if (field === "api_key" || field === "provider") return;
            config[field] = value as string | number;
        });
        return config;
    };

    const onSubmit = async (data: FormValues) => {
        setApiError(null);
        setIsSaving(true);

        try {
            if (mode === 'override') {
                // Build model_overrides for enabled services only
                const modelOverrides: Record<string, unknown> = {};
                const services = isRealtime ? ["realtime", "llm"] : ["llm", "tts", "stt"];
                for (const svc of services) {
                    if (enabledOverrides[svc]) {
                        modelOverrides[svc] = buildServiceConfig(svc as ServiceSegment, data);
                    }
                }
                // Include is_realtime if it differs from global
                const globalIsRealtime = !!(userConfig as Record<string, unknown> | null)?.is_realtime;
                if (isRealtime !== globalIsRealtime) {
                    modelOverrides.is_realtime = isRealtime;
                }
                await onSave({
                    model_overrides: Object.keys(modelOverrides).length > 0 ? modelOverrides : undefined,
                });
            } else {
                // Global mode: save all services
                const saveConfig: Record<string, unknown> = {
                    llm: buildServiceConfig("llm", data),
                    tts: buildServiceConfig("tts", data),
                    stt: buildServiceConfig("stt", data),
                    is_realtime: isRealtime,
                };
                if (serviceProviders.realtime) {
                    saveConfig.realtime = buildServiceConfig("realtime", data);
                }
                const embeddingsKeys = apiKeys.embeddings.map(k => k.trim()).filter(k => k.length > 0);
                if (embeddingsKeys.length > 0) {
                    saveConfig.embeddings = buildServiceConfig("embeddings", data);
                }
                await onSave(saveConfig);
            }
            setApiError(null);
        } catch (error: unknown) {
            if (error instanceof Error) {
                setApiError(error.message);
            } else {
                setApiError('An unknown error occurred');
            }
        } finally {
            setIsSaving(false);
        }
    };

    const getConfigFields = (service: ServiceSegment): string[] => {
        const currentProvider = serviceProviders[service];
        const providerSchema = schemas?.[service]?.[currentProvider];
        if (!providerSchema) return [];
        return Object.keys(providerSchema.properties).filter(
            field => field !== "provider" && field !== "api_key"
        );
    };

    const renderServiceFields = (service: ServiceSegment) => {
        const currentProvider = serviceProviders[service];
        const providerSchema = schemas?.[service]?.[currentProvider];
        const allProviders = schemas?.[service] ? Object.keys(schemas[service]) : [];
        // Managed is chosen by the toggle above, not from this list — leaving
        // "Decibyl" among the vendors would mean two controls setting the same
        // thing and disagreeing.
        const availableProviders = keysFromVault
            ? allProviders.filter((p) => p !== MANAGED)
            : allProviders;
        const configFields = getConfigFields(service);
        const managed = currentProvider === MANAGED;
        const canBeManaged = keysFromVault && Boolean(schemas?.[service]?.[MANAGED]);

        return (
            <div className="space-y-6">
                {canBeManaged && (
                    <div className="space-y-2">
                        <Label>Who provides this model</Label>
                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                onClick={() => handleProviderChange(service, MANAGED)}
                                className={`rounded-md border px-3 py-2.5 text-left text-sm transition ${
                                    managed
                                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                                        : "border-input hover:border-primary/40"
                                }`}
                            >
                                <span className="font-medium">Decibyl provides it</span>
                                <span className="mt-0.5 block text-xs text-muted-foreground">
                                    No key needed. Billed at the published rate.
                                </span>
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    if (!managed) return;
                                    const fallback =
                                        configurationDefaults?.default_providers?.[service] ||
                                        availableProviders[0];
                                    if (fallback) handleProviderChange(service, fallback);
                                }}
                                className={`rounded-md border px-3 py-2.5 text-left text-sm transition ${
                                    !managed
                                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                                        : "border-input hover:border-primary/40"
                                }`}
                            >
                                <span className="font-medium">My own key</span>
                                <span className="mt-0.5 block text-xs text-muted-foreground">
                                    Runs on a key you store under Provider Keys.
                                </span>
                            </button>
                        </div>
                    </div>
                )}

                <div className="grid grid-cols-2 gap-4">
                    <div className={`space-y-2 ${managed && keysFromVault ? "col-span-2" : ""}`}>
                        <Label>{managed && keysFromVault ? "Tier" : "Provider"}</Label>
                        {managed && keysFromVault ? (
                            <p className="rounded-md border border-input bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                                Decibyl picks the vendor for this tier and can move it without
                                changing your agents.
                            </p>
                        ) : (
                        <Select
                            value={currentProvider}
                            onValueChange={(providerName) => {
                                handleProviderChange(service, providerName);
                            }}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select provider" />
                            </SelectTrigger>
                            <SelectContent>
                                {availableProviders.map((provider) => (
                                    <SelectItem key={provider} value={provider}>
                                        {getProviderDisplayName(provider, schemas?.[service]?.[provider])}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        )}
                        {(providerSchema?.description || providerSchema?.provider_docs_url) && (
                            <p className="text-xs text-muted-foreground">
                                {providerSchema?.description}{" "}
                                {providerSchema?.provider_docs_url && (
                                    <a
                                        href={providerSchema.provider_docs_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-0.5 underline"
                                    >
                                        Learn more <ExternalLink className="h-3 w-3" />
                                    </a>
                                )}
                            </p>
                        )}
                    </div>

                    {currentProvider && providerSchema && configFields[0] && (
                        <div className="space-y-2">
                            <Label className="capitalize">{configFields[0].replace(/_/g, ' ')}</Label>
                            {renderField(service, configFields[0], providerSchema)}
                        </div>
                    )}
                </div>

                {currentProvider && providerSchema && configFields.length > 1 && (
                    <div className="grid grid-cols-2 gap-4">
                        {configFields.slice(1).map((field) => {
                            const fieldSchema = providerSchema.properties[field];
                            const actualFieldSchema = fieldSchema?.$ref && providerSchema.$defs
                                ? providerSchema.$defs[fieldSchema.$ref.split('/').pop() || '']
                                : fieldSchema;
                            const fullWidth = actualFieldSchema?.multiline;
                            return (
                                <div key={field} className={`space-y-2 ${fullWidth ? "col-span-2" : ""}`}>
                                    <Label className="capitalize">{field.replace(/_/g, ' ')}</Label>
                                    {renderField(service, field, providerSchema)}
                                </div>
                            );
                        })}
                    </div>
                )}

                {/* Keys do not belong on this screen. They live in the vault at
                    /provider-keys, and are resolved at dial time — so all this
                    needs to say is whether the one this slot depends on is
                    actually there. Showing an input here is what made every
                    model screen a key-entry screen, and what discarded a pasted
                    key when you changed provider. */}
                {keysFromVault && !managed && currentProvider && providerSchema?.properties.api_key && (
                    <VaultKeyStatus
                        service={service}
                        provider={currentProvider}
                        providerLabel={getProviderDisplayName(currentProvider, providerSchema) || currentProvider}
                        keysHeld={keysHeld}
                    />
                )}

                {!keysFromVault && currentProvider && providerSchema && providerSchema.properties.api_key && (
                    <div className="space-y-2">
                        <Label>{mode === 'override' ? 'API Key (leave empty to use global)' : 'API Key(s)'}</Label>
                        {renderFieldDescription("api_key", providerSchema)}
                        {apiKeys[service].map((key, index) => (
                            <div key={index} className="flex gap-2">
                                <Input
                                    type="text"
                                    placeholder="Enter API key"
                                    value={key}
                                    onChange={(e) => {
                                        const newKeys = [...apiKeys[service]];
                                        newKeys[index] = e.target.value;
                                        setApiKeys(prev => ({ ...prev, [service]: newKeys }));
                                    }}
                                />
                                {apiKeys[service].length > 1 && (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="shrink-0"
                                        onClick={() => {
                                            setApiKeys(prev => ({
                                                ...prev,
                                                [service]: prev[service].filter((_, i) => i !== index),
                                            }));
                                        }}
                                    >
                                        <X className="h-4 w-4" />
                                    </Button>
                                )}
                            </div>
                        ))}
                        {mode !== 'override' && (
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setApiKeys(prev => ({
                                        ...prev,
                                        [service]: [...prev[service], ""],
                                    }));
                                }}
                            >
                                <Plus className="h-4 w-4 mr-1" /> Add API Key
                            </Button>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const renderFieldDescription = (field: string, providerSchema: ProviderSchema) => {
        const schema = providerSchema.properties[field];
        if (!schema) return null;
        const actualSchema = schema.$ref && providerSchema.$defs
            ? providerSchema.$defs[schema.$ref.split('/').pop() || '']
            : schema;
        if (!actualSchema?.description && !actualSchema?.docs_url) return null;
        return (
            <p className="text-xs text-muted-foreground">
                {actualSchema?.description}{" "}
                {actualSchema?.docs_url && (
                    <a
                        href={actualSchema.docs_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 underline"
                    >
                        Supported languages <ExternalLink className="h-3 w-3" />
                    </a>
                )}
            </p>
        );
    };

    const renderField = (service: ServiceSegment, field: string, providerSchema: ProviderSchema) => {
        return (
            <>
                {renderFieldInput(service, field, providerSchema)}
                {renderFieldDescription(field, providerSchema)}
            </>
        );
    };

    const renderFieldInput = (service: ServiceSegment, field: string, providerSchema: ProviderSchema) => {
        const schema = providerSchema.properties[field];
        const actualSchema = schema.$ref && providerSchema.$defs
            ? providerSchema.$defs[schema.$ref.split('/').pop() || '']
            : schema;
        const dropdownOptions = getSchemaDropdownOptions(
            actualSchema,
            watch(`${service}_model`) as string | undefined,
        );

        if (service === "tts" && field === "voice" && !actualSchema?.allow_custom_input) {
            if (!dropdownOptions) {
                return (
                    <VoiceSelector
                        provider={serviceProviders.tts}
                        value={watch(`${service}_${field}`) as string || ""}
                        onChange={(voiceId) => {
                            setValue(`${service}_${field}`, voiceId, { shouldDirty: true });
                        }}
                        model={watch("tts_model") as string || undefined}
                    />
                );
            }
        }

        if (actualSchema?.allow_custom_input && dropdownOptions && dropdownOptions.length > 0) {
            const fieldKey = `${service}_${field}`;
            const currentValue = watch(fieldKey) as string || "";
            const options = dropdownOptions;

            if (isCustomInput[fieldKey]) {
                return (
                    <div className="space-y-2">
                        <Input
                            type="text"
                            placeholder={`Enter ${field}`}
                            value={currentValue}
                            onChange={(e) => {
                                setValue(fieldKey, e.target.value, { shouldDirty: true });
                            }}
                        />
                        <div className="flex items-center space-x-2">
                            <Checkbox
                                id={`custom-input-${fieldKey}`}
                                checked={true}
                                onCheckedChange={(checked) => {
                                    setIsCustomInput(prev => ({ ...prev, [fieldKey]: checked as boolean }));
                                    if (!checked && options.length > 0) {
                                        setValue(fieldKey, options[0], { shouldDirty: true });
                                    }
                                }}
                            />
                            <Label htmlFor={`custom-input-${fieldKey}`} className="text-sm font-normal cursor-pointer">
                                Enter Custom Value
                            </Label>
                        </div>
                    </div>
                );
            }

            return (
                <div className="space-y-2">
                    <Select
                        value={currentValue}
                        onValueChange={(value) => {
                            if (!value) return;
                            setValue(fieldKey, value, { shouldDirty: true });
                        }}
                    >
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder={`Select ${field}`} />
                        </SelectTrigger>
                        <SelectContent>
                            {options.map((value: string) => (
                                <SelectItem key={value} value={value}>
                                    {value}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <div className="flex items-center space-x-2">
                        <Checkbox
                            id={`custom-input-${fieldKey}-dropdown`}
                            checked={false}
                            onCheckedChange={(checked) => {
                                setIsCustomInput(prev => ({ ...prev, [fieldKey]: checked as boolean }));
                            }}
                        />
                        <Label htmlFor={`custom-input-${fieldKey}-dropdown`} className="text-sm font-normal cursor-pointer">
                            Enter Custom Value
                        </Label>
                    </div>
                </div>
            );
        }

        if (dropdownOptions && dropdownOptions.length > 0) {
            const getDisplayName = (value: string) => {
                if (field === "language") {
                    return LANGUAGE_DISPLAY_NAMES[value] || value;
                }
                if (field === "voice") {
                    return VOICE_DISPLAY_NAMES[value] || value.charAt(0).toUpperCase() + value.slice(1);
                }
                return value;
            };

            return (
                <Select
                    value={watch(`${service}_${field}`) as string || ""}
                    onValueChange={(value) => {
                        if (!value) return;
                        setValue(`${service}_${field}`, value, { shouldDirty: true });
                    }}
                >
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder={`Select ${field}`} />
                    </SelectTrigger>
                    <SelectContent>
                        {dropdownOptions.map((value: string) => (
                            <SelectItem key={value} value={value}>
                                {getDisplayName(value)}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            );
        }

        if (actualSchema?.multiline) {
            return (
                <Textarea
                    rows={6}
                    className="font-mono text-xs"
                    placeholder={`Enter ${field}`}
                    {...register(`${service}_${field}`, {
                        required: service !== "embeddings" && providerSchema.required?.includes(field),
                    })}
                />
            );
        }

        return (
            <Input
                type={actualSchema?.type === "number" ? "number" : "text"}
                {...(actualSchema?.type === "number" && { step: "any" })}
                placeholder={`Enter ${field}`}
                {...register(`${service}_${field}`, {
                    required: service !== "embeddings" && providerSchema.required?.includes(field),
                    valueAsNumber: actualSchema?.type === "number"
                })}
            />
        );
    };

    const handleOverrideToggle = (service: string, enabled: boolean) => {
        setEnabledOverrides(prev => ({ ...prev, [service]: enabled }));
    };

    const renderOverrideToggle = (service: ServiceSegment, label: string) => {
        const globalVal = (userConfig as Record<string, unknown> | null)?.[service] as Record<string, unknown> | null | undefined;
        const isEnabled = enabledOverrides[service];
        const globalProvider = globalVal?.provider as string | undefined;
        const globalProviderSchema = globalProvider ? schemas?.[service]?.[globalProvider] : undefined;

        return (
            <div className="flex items-center justify-between p-3 border rounded-md bg-muted/20 mb-4">
                <div className="space-y-0.5">
                    <Label htmlFor={`override-${service}`} className="text-sm cursor-pointer font-medium">
                        Override {label}
                    </Label>
                    {!isEnabled && (
                        <p className="text-xs text-muted-foreground">
                            Using global: {getGlobalSummary(globalVal, globalProviderSchema)}
                        </p>
                    )}
                </div>
                <Switch
                    id={`override-${service}`}
                    checked={isEnabled}
                    onCheckedChange={(checked) => handleOverrideToggle(service, checked)}
                />
            </div>
        );
    };

    const getVisibleTabs = () => {
        if (mode === 'override') {
            return isRealtime ? OVERRIDE_REALTIME_TABS : OVERRIDE_STANDARD_TABS;
        }
        return isRealtime ? REALTIME_TABS : STANDARD_TABS;
    };

    const visibleTabs = getVisibleTabs();
    const defaultTab = isRealtime ? "realtime" : "llm";

    return (
        <form onSubmit={handleSubmit(onSubmit)}>
            {/* Realtime toggle — hidden when the parent locks the mode (v2 tabs) */}
            {forceRealtime === undefined && (
                <div className="flex items-center justify-between mb-4 p-4 border rounded-lg">
                    <div>
                        <Label htmlFor="realtime-toggle" className="text-sm font-medium">
                            Realtime Mode
                        </Label>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Uses a single speech-to-speech model (no separate STT/TTS). An LLM is still required for variable extraction and QA.
                        </p>
                    </div>
                    <Switch
                        id="realtime-toggle"
                        checked={isRealtime}
                        onCheckedChange={setIsRealtime}
                    />
                </div>
            )}

            {/* What this stack costs, before the first call rather than on
                the first invoice. Reads the live selection, so switching model
                moves the number immediately. */}
            <CostPerMinuteBar
                className="mb-4"
                stack={{
                    ...pricedSlot("stt", isRealtime ? null : serviceProviders.stt, watch("stt_model") as string),
                    ...pricedSlot("llm", serviceProviders.llm, watch("llm_model") as string),
                    ...pricedSlot("tts", isRealtime ? null : serviceProviders.tts, watch("tts_model") as string),
                }}
            />

            <Card>
                <CardContent className="pt-6">
                    <Tabs key={defaultTab} defaultValue={defaultTab} className="w-full">
                        <TabsList className="grid w-full mb-6" style={{ gridTemplateColumns: `repeat(${visibleTabs.length}, 1fr)` }}>
                            {visibleTabs.map(({ key, label }) => (
                                <TabsTrigger key={key} value={key}>
                                    {label}
                                </TabsTrigger>
                            ))}
                        </TabsList>

                        {visibleTabs.map(({ key, label }) => (
                            <TabsContent key={key} value={key} className="mt-0">
                                {mode === 'override' && renderOverrideToggle(key, label)}
                                {(mode === 'global' || enabledOverrides[key]) && renderServiceFields(key)}
                            </TabsContent>
                        ))}
                    </Tabs>
                </CardContent>
            </Card>

            {apiError && <p className="text-red-500 mt-4">{apiError}</p>}

            <Button type="submit" className="w-full mt-6" disabled={isSaving}>
                {isSaving ? "Saving..." : (submitLabel || "Save Configuration")}
            </Button>
        </form>
    );
}
