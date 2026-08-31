"use client";

import { AlertTriangle, ChevronRight, ExternalLink, KeyRound, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";

import {
    getCarriageBasisApiV1AgentOptionsCarriageGet,
    getCatalogueApiV1AgentOptionsCatalogueGet,
    getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet,
} from '@/client/sdk.gen';
import { CostPerMinuteBar } from "@/components/CostPerMinuteBar";
import {
    CatalogueModelSelect,
    OwnKeyModelSelect,
    runsOnPlatformKey,
    type SlotCatalogue,
} from "@/components/ModelSlotSelect";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectGroup,
    SelectItem,
    SelectLabel,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { VoiceSelector } from "@/components/VoiceSelector";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { useUserConfig } from "@/context/UserConfigContext";
import { pricedStack } from "@/lib/billing/pricedStack";
import type { ModelOverrides } from "@/types/workflow-configurations";

export type ServiceSegment = "llm" | "tts" | "stt" | "embeddings" | "realtime";

export interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    // Pydantic emits these from Field(ge=..., le=...). Every provider class in
    // configuration/registry declares its own range -- ElevenLabs speed is
    // 0.1-2.0, Cartesia's is 0.6-1.5 -- and until they were read here the form
    // rendered an unbounded box and let the server reject the value.
    minimum?: number;
    maximum?: number;
    // Pydantic's gt=/lt= land here instead. MiniMax's temperature is gt=0
    // because MiniMax rejects 0, so reading only `minimum` would leave that
    // one field as an unbounded box while every sibling got a slider.
    exclusiveMinimum?: number;
    exclusiveMaximum?: number;
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

/** The two halves of an agent: what it hears and says, and what it thinks with.
 *
 * A flat strip of four vendor slots — LLM, Voice, Transcriber, Embedding — is
 * the API's vocabulary, not the buyer's. It also put the two slots that decide
 * how an agent *sounds* at opposite ends of the row, with the one that decides
 * how it *thinks* between them, so reading "what is my voice stack" meant
 * knowing which two of the four to look at.
 *
 * Grouped, the tab strip states the architecture instead of enumerating it:
 * ears and mouth under Voice, brain and memory under Model. Speech-to-speech
 * collapses the Voice group to one entry, which is exactly what it does to the
 * pipeline.
 */
type TabGroup = "voice" | "model";

const TAB_GROUP_LABELS: Record<TabGroup, string> = {
    voice: "Voice",
    model: "Model",
};

type TabSpec = { key: ServiceSegment; label: string; group: TabGroup };

// The slot names themselves are unchanged. Every docs page calls the pipeline
// "Transcriber -> Model -> Voice", and renaming a tab so the group heading
// above it reads better would put the product and its documentation into two
// vocabularies for one thing — which is the more expensive kind of confusing.
const STANDARD_TABS: TabSpec[] = [
    { key: "stt", label: "Transcriber", group: "voice" },
    { key: "tts", label: "Voice", group: "voice" },
    { key: "llm", label: "LLM", group: "model" },
    { key: "embeddings", label: "Embedding", group: "model" },
];

const REALTIME_TABS: TabSpec[] = [
    // One model hears and speaks, so there is nothing else in the Voice group.
    { key: "realtime", label: "Speech to Speech", group: "voice" },
    // Still required: variable extraction and QA run on the transcript after
    // the call, and those are text tasks.
    { key: "llm", label: "LLM", group: "model" },
    { key: "embeddings", label: "Embedding", group: "model" },
];

const OVERRIDE_STANDARD_TABS: TabSpec[] = [
    { key: "stt", label: "Transcriber", group: "voice" },
    { key: "tts", label: "Voice", group: "voice" },
    { key: "llm", label: "LLM", group: "model" },
];

const OVERRIDE_REALTIME_TABS: TabSpec[] = [
    { key: "realtime", label: "Speech to Speech", group: "voice" },
    { key: "llm", label: "LLM", group: "model" },
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
     * Which slots can be served as managed today, as `{ stt: true, tts: false }`.
     *
     * A slot the platform holds no key for is shown as coming soon rather than
     * offered — picking it would save cleanly, build an agent, and only fail
     * when the call is placed. A slot missing from this map is treated as
     * available, so a response that predates the field behaves as before.
     */
    managedAvailable?: Record<string, boolean>;
    /**
     * Real providers each slot can run on Decibyl's key, as
     * `{ llm: ["openai", "google"], stt: ["sarvam"] }`.
     *
     * The catalog counterpart to `managedAvailable`/`managedUpstream`: instead
     * of a fixed tier, a customer picks the exact vendor and model BYOK would
     * offer, authenticated with our key instead of theirs. An empty list means
     * nothing to offer yet for that slot, and the toggle falls back to the
     * tier system above.
     */
    platformKeyProviders?: Record<string, string[]>;
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

/** Slots `GET /agent-options/catalogue` answers for. Embeddings is not sold. */
const CATALOGUE_SLOTS: ServiceSegment[] = ["stt", "llm", "tts", "realtime"];

/**
 * Which credential a slot authenticates with.
 *
 * Embeddings and realtime run on the LLM key: a vendor issues one key for chat
 * and embeddings alike, and a realtime model is a language model that speaks.
 * The backend resolves them the same way.
 */
function credentialComponentFor(service: ServiceSegment): ServiceSegment {
    return service === "embeddings" || service === "realtime" ? "llm" : service;
}

/**
 * Whether a slot offers "Decibyl provides it / My own key" as a choice.
 *
 * Off: we manage the providers, and a customer's own key covers only what we do
 * not sell. See `canBeManaged` below for the full reasoning.
 */
const BYOK_SLOT_CHOICE_ENABLED = false;

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
    const held = keysHeld?.[credentialComponentFor(service)] ?? [];
    const hasKey = held.includes(provider);

    if (hasKey) {
        return (
            <div className="flex items-start gap-2 rounded-md border border-input bg-muted/40 px-3 py-2.5 text-sm">
                <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="text-muted-foreground">
                    Runs on your stored {providerLabel} key.{" "}
                    <a href="/integrations" className="underline">Manage keys</a>
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
                <a href="/integrations" className="underline">Add the key</a>, or switch
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
    managedAvailable,
    platformKeyProviders,
    keysFromVault,
    keysHeld,
}: ServiceConfigurationFormProps) {

    // The carrier whose minutes will land on *this account's* invoice, which
    // is not the same as the carrier it dials on. Both mistakes are available
    // here and this screen used to make the second one: pricing the default
    // outbound configuration's provider whatever it was quoted carriage to an
    // account dialling on its own Twilio, which is already being billed by
    // Twilio — the same double charge `carriage.py` refuses on the invoice,
    // appearing on the estimate instead. Omitting it entirely is the opposite
    // error and no better: carriage is an exact per-minute rate and routinely
    // a third of the cost of a minute.
    const [telephonyProvider, setTelephonyProvider] = useState<string | null>(null);
    //: What to say under the price about what it excludes. Empty while the
    //: answer is in flight — a caveat that flickers is worse than a late one.
    const [carriageNote, setCarriageNote] = useState<string>("");

    // What Decibyl sells, per slot, with what each model costs a minute.
    //
    // Null until it arrives, and only ever fetched in vault mode: the legacy
    // per-workflow override screen carries its own keys inline and is a BYOK
    // surface by construction, so a managed catalogue has nothing to say
    // there. Null means "we do not know yet" and the form keeps the registry
    // list; an empty list for a slot means we sell nothing for it, which is a
    // different thing and says so on the screen.
    const [catalogue, setCatalogue] = useState<SlotCatalogue | null>(null);

    // Per-slot, and undefined until the operator touches it — so the fold
    // starts wherever `hasNonDefaultFields` says it should and only stops
    // following that once somebody has actually opened or closed it.
    const [advancedOpen, setAdvancedOpen] = useState<
        Partial<Record<ServiceSegment, boolean>>
    >({});

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
    // Whether each slot runs its real provider on Decibyl's key rather than
    // the account's. Kept as its own piece of state, same as serviceProviders,
    // rather than a registered form field -- it's never rendered as a generic
    // input, only driven by the "Who provides this model" toggle below.
    const [usePlatformKey, setUsePlatformKey] = useState<Record<ServiceSegment, boolean>>({
        llm: false,
        tts: false,
        stt: false,
        embeddings: false,
        realtime: false,
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

    // Read once, not per keystroke: the estimate re-prices as the model
    // selection changes, and the carrier does not change with it.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            const response = await getCarriageBasisApiV1AgentOptionsCarriageGet();
            if (cancelled || response.error) return;
            const basis = response.data as unknown as {
                provider?: string | null;
                explanation?: string;
            };
            setTelephonyProvider(basis?.provider ?? null);
            setCarriageNote(basis?.explanation ?? "");
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        if (!keysFromVault) return;
        let cancelled = false;
        (async () => {
            const response = await getCatalogueApiV1AgentOptionsCatalogueGet();
            if (cancelled || response.error) return;
            const payload = response.data as unknown as { catalogue?: SlotCatalogue };
            setCatalogue(payload?.catalogue ?? {});
        })();
        return () => {
            cancelled = true;
        };
    }, [keysFromVault]);

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

            const selectedUsePlatformKey: Record<ServiceSegment, boolean> = {
                llm: false,
                tts: false,
                stt: false,
                embeddings: false,
                realtime: false,
            };

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
                        } else if (field !== "provider" && field !== "use_platform_key") {
                            defaultValues[`${service}_${field}`] = value as string | number | boolean;
                        }
                    });
                    selectedProviders[service] = src.provider as string;
                    selectedUsePlatformKey[service] = Boolean(src.use_platform_key);
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            const key = `${service}_${field}`;
                            if (field !== "provider" && field !== "api_key" && field !== "use_platform_key" && schema.default !== undefined && !(key in defaultValues)) {
                                defaultValues[key] = schema.default;
                            }
                        });
                    }
                } else {
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            if (field !== "provider" && field !== "use_platform_key" && schema.default !== undefined) {
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
            setUsePlatformKey(selectedUsePlatformKey);
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
                if (field !== "provider" && field !== "use_platform_key" && schema.default !== undefined) {
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

    // One answer for both the card and the payload. Synchronising this derived
    // value through an effect let Save observe the old flag while the card was
    // already showing Decibyl as the payer.
    const slotUsesPlatformKey = (service: ServiceSegment) => {
        if (!keysFromVault || !catalogue || !CATALOGUE_SLOTS.includes(service)) {
            return usePlatformKey[service];
        }
        const provider = serviceProviders[service];
        return runsOnPlatformKey({
            sells: (catalogue[service] ?? []).some((option) => option.provider === provider),
            holdsOwnKey: (keysHeld?.[credentialComponentFor(service)] ?? []).includes(provider),
            saved: usePlatformKey[service] === true,
        });
    };

    const buildServiceConfig = (service: ServiceSegment, data: FormValues) => {
        const config: Record<string, string | number | string[] | boolean> = {
            provider: serviceProviders[service],
            use_platform_key: slotUsesPlatformKey(service),
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
            if (field === "api_key" || field === "provider" || field === "use_platform_key") return;
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
        // use_platform_key is driven entirely by the "Who provides this
        // model" toggle, never rendered as a generic input.
        return Object.keys(providerSchema.properties).filter(
            field => field !== "provider" && field !== "api_key" && field !== "use_platform_key"
        );
    };

    /** Whether any setting past the model differs from what the schema chose.
     *
     * Decides whether the Advanced fold starts open. A value somebody set on
     * purpose, hidden behind a collapsed section they have to know to click,
     * is worse than a section that is open more often than it needs to be:
     * they would be debugging behaviour they configured and cannot see.
     */
    const hasNonDefaultFields = (
        service: ServiceSegment,
        providerSchema: ProviderSchema,
    ): boolean =>
        getConfigFields(service)
            .slice(1)
            .some((field) => {
                const schema = providerSchema.properties[field];
                const actual = schema?.$ref && providerSchema.$defs
                    ? providerSchema.$defs[schema.$ref.split("/").pop() || ""]
                    : schema;
                const current = watch(`${service}_${field}`);
                if (current === undefined || current === "") return false;
                return String(current) !== String(actual?.default ?? "");
            });

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

        // Two ways a slot ends up on Decibyl's key: the older fixed tier
        // (provider="decibyl", model is a tier name), or a real vendor+model
        // chosen directly, same as BYOK, with use_platform_key=true. A slot
        // can only be in one of the two -- see managed_resolution.apply for
        // the backend side of this split.
        const legacyManaged = currentProvider === MANAGED;
        const directManaged = usePlatformKey[service] === true && !legacyManaged;
        const managed = legacyManaged || directManaged;

        const platformProviders = platformKeyProviders?.[service] ?? [];
        const hasDirectCatalog = platformProviders.length > 0;
        const hasLegacyTier = Boolean(schemas?.[service]?.[MANAGED]);
        // "Who provides this model" is no longer a question we ask.
        //
        // Decibyl manages the providers: it curates them, holds the keys, prices
        // the models and sells them. A customer's own key is the escape hatch
        // for something we do not offer, and it belongs on the Provider Keys
        // screen where a key is added — not as a per-slot toggle that made every
        // model choice two decisions instead of one.
        //
        // Behind a flag rather than deleted: the mechanism underneath is
        // unchanged and enterprise accounts are expected to want it back. A
        // stored configuration that already names a customer-keyed provider
        // keeps working either way — this hides the question, it does not
        // rewrite anyone's agent.
        const canBeManaged =
            BYOK_SLOT_CHOICE_ENABLED &&
            keysFromVault &&
            (hasDirectCatalog || hasLegacyTier);
        // On the direct path, only vendors we actually hold a platform key for
        // are offered — picking one we don't would save cleanly and fail at
        // dial time, the same trade-off the tier system already avoids.
        const directProviderOptions = availableProviders.filter((p) =>
            platformProviders.includes(p)
        );

        // ---- What this account may actually choose, for this slot ----------
        //
        // Two lists, and between them they are the whole answer: models we
        // sell, and models their own key reaches. The registry's `examples`
        // is neither — it is every vendor this codebase has ever integrated,
        // including the ones we hold no key for and nobody has priced.
        const slotCatalogue = CATALOGUE_SLOTS.includes(service)
            ? (catalogue?.[service] ?? [])
            : [];
        // Sellable *and* buildable. A catalogue row for a vendor the registry
        // cannot construct would offer a model that saves and never runs.
        const sellableProviders = Array.from(
            new Set(slotCatalogue.map((option) => option.provider)),
        ).filter((provider) => availableProviders.includes(provider));
        const credentialComponent = credentialComponentFor(service);
        const heldProviders = keysHeld?.[credentialComponent] ?? [];
        // Their own key covers what we do not sell — the same rule
        // `GET /provider-keys/models` applies to the model list inside it.
        const ownKeyProviders = availableProviders.filter(
            (provider) =>
                heldProviders.includes(provider) &&
                !sellableProviders.includes(provider),
        );
        // Null catalogue means the request has not come back; the registry
        // list stands until it does rather than blinking through an empty one.
        const catalogueMode =
            Boolean(keysFromVault) &&
            catalogue !== null &&
            CATALOGUE_SLOTS.includes(service) &&
            (sellableProviders.length > 0 || ownKeyProviders.length > 0);
        // A stored slot that named a vendor we also sell, with its own key:
        // that stays their choice until they change it here. Flipping it to
        // our key because the vendor happens to be in our catalogue would
        // move a live agent onto our bill without anyone deciding to.
        const runsOnOurKey = slotUsesPlatformKey(service);

        const chooseModel = (provider: string, model: string, onOurKey: boolean) => {
            if (serviceProviders[service] !== provider) {
                handleProviderChange(service, provider);
            }
            setValue(`${service}_model`, model, { shouldDirty: true });
            setUsePlatformKey((prev) => ({ ...prev, [service]: onOurKey }));
        };

        const chooseProvider = (provider: string) => {
            if (provider === MANAGED) {
                handleProviderChange(service, MANAGED);
                setUsePlatformKey((prev) => ({ ...prev, [service]: false }));
                return;
            }
            const onOurKey = sellableProviders.includes(provider);
            handleProviderChange(service, provider);
            setUsePlatformKey((prev) => ({ ...prev, [service]: onOurKey }));
            // Land on the cheapest thing we sell rather than on whatever the
            // schema's default happens to be — the schema default is a vendor
            // recommendation, and on the managed path it can be a model we do
            // not offer at all.
            const first = slotCatalogue.find((option) => option.provider === provider);
            setValue(`${service}_model`, onOurKey ? (first?.model ?? "") : "", {
                shouldDirty: true,
            });
        };

        // Ready to click, independent of which mechanism it lands on. Absent
        // legacy availability means available: a defaults response that
        // predates that field should keep behaving as it did.
        const managedReady = hasDirectCatalog || managedAvailable?.[service] !== false;
        // Already selected but no longer servable — the key was removed, or
        // (direct path) the provider fell out of the catalog, or (tier path)
        // the tier moved to a provider we no longer hold. Say so instead of
        // letting it fail at dial time.
        const managedStranded =
            (legacyManaged && managedAvailable?.[service] === false) ||
            (directManaged && !platformProviders.includes(currentProvider));

        return (
            <div className="space-y-6">
                {canBeManaged && (
                    <div className="space-y-2">
                        <Label className="text-sm font-semibold tracking-[-0.01em] text-foreground">
                            Who provides this model
                        </Label>
                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                disabled={!managedReady}
                                aria-disabled={!managedReady}
                                onClick={() => {
                                    if (!managedReady) return;
                                    if (hasDirectCatalog) {
                                        setUsePlatformKey((prev) => ({ ...prev, [service]: true }));
                                        const fallback = platformProviders.includes(currentProvider)
                                            ? currentProvider
                                            : (configurationDefaults?.default_providers?.[service] &&
                                                  platformProviders.includes(
                                                      configurationDefaults.default_providers[service]!,
                                                  )
                                                ? configurationDefaults.default_providers[service]!
                                                : platformProviders[0]);
                                        if (fallback && fallback !== currentProvider) {
                                            handleProviderChange(service, fallback);
                                        }
                                    } else {
                                        // No platform-held provider for this slot yet — fall
                                        // back to the tier system, unchanged from before.
                                        handleProviderChange(service, MANAGED);
                                    }
                                }}
                                className={`rounded-md border px-3 py-2.5 text-left text-sm transition ${
                                    !managedReady
                                        ? "cursor-not-allowed border-dashed border-input bg-muted/30 opacity-70"
                                        : managed
                                          ? "border-primary bg-primary/5 ring-1 ring-primary"
                                          : "border-input hover:border-primary/40"
                                }`}
                            >
                                <span className="flex items-center gap-2 font-medium">
                                    Decibyl provides it
                                    {!managedReady && (
                                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                                            Coming soon
                                        </span>
                                    )}
                                </span>
                                <span className="mt-0.5 block text-xs text-muted-foreground">
                                    {managedReady
                                        ? "No key needed. Billed at the published rate."
                                        : "Not available yet. Use your own key for now."}
                                </span>
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    if (!managed) return;
                                    setUsePlatformKey((prev) => ({ ...prev, [service]: false }));
                                    if (legacyManaged) {
                                        const fallback =
                                            configurationDefaults?.default_providers?.[service] ||
                                            availableProviders[0];
                                        if (fallback) handleProviderChange(service, fallback);
                                    }
                                    // On the direct path the provider is already a real
                                    // vendor — flipping the flag is enough, the same
                                    // dropdown value just now needs its own key.
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
                        {managedStranded && (
                            <p className="text-xs text-amber-600 dark:text-amber-500">
                                This slot is set to Decibyl, but we cannot serve it right
                                now. Switch it to your own key, or calls using this agent
                                will fail.
                            </p>
                        )}
                    </div>
                )}

                <div className="grid grid-cols-2 gap-4">
                    <div className={`space-y-2 ${!catalogueMode && legacyManaged && keysFromVault ? "col-span-2" : ""}`}>
                        <Label>{!catalogueMode && legacyManaged && keysFromVault ? "Tier" : "Provider"}</Label>
                        {catalogueMode ? (
                            /* Only what this account can actually run: vendors we
                               sell, and vendors their own key reaches. A vendor in
                               neither group is not a choice with a caveat — it is
                               one that saves cleanly and fails on the first call. */
                            <Select
                                value={currentProvider}
                                onValueChange={(providerName) => chooseProvider(providerName)}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Select provider" />
                                </SelectTrigger>
                                <SelectContent>
                                    {legacyManaged && (
                                        /* Kept selectable so a slot already on the
                                           automatic tier renders its own value. It
                                           is not offered to anyone not already on
                                           it: below, the specific models are. */
                                        <SelectItem value={MANAGED}>
                                            Decibyl — automatic
                                        </SelectItem>
                                    )}
                                    {sellableProviders.length > 0 && (
                                        <SelectGroup>
                                            <SelectLabel>Decibyl provides these</SelectLabel>
                                            {sellableProviders.map((provider) => (
                                                <SelectItem key={provider} value={provider}>
                                                    {getProviderDisplayName(provider, schemas?.[service]?.[provider])}
                                                </SelectItem>
                                            ))}
                                        </SelectGroup>
                                    )}
                                    {ownKeyProviders.length > 0 && (
                                        <SelectGroup>
                                            <SelectLabel>On your own key</SelectLabel>
                                            {ownKeyProviders.map((provider) => (
                                                <SelectItem key={provider} value={provider}>
                                                    {getProviderDisplayName(provider, schemas?.[service]?.[provider])}
                                                </SelectItem>
                                            ))}
                                        </SelectGroup>
                                    )}
                                </SelectContent>
                            </Select>
                        ) : legacyManaged && keysFromVault ? (
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
                                {(directManaged ? directProviderOptions : availableProviders).map((provider) => (
                                    <SelectItem key={provider} value={provider}>
                                        {getProviderDisplayName(provider, schemas?.[service]?.[provider])}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        )}
                        {catalogueMode && legacyManaged && (
                            <p className="text-xs text-muted-foreground">
                                This slot runs on whichever vendor Decibyl points the tier
                                at. Pick a provider above to name a specific model and see
                                what it costs.
                            </p>
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
                            {catalogueMode && configFields[0] === "model" && !legacyManaged ? (
                                runsOnOurKey ? (
                                    <CatalogueModelSelect
                                        options={slotCatalogue.filter(
                                            (option) => option.provider === currentProvider,
                                        )}
                                        value={(watch(`${service}_model`) as string) || ""}
                                        onChange={(model) =>
                                            chooseModel(currentProvider, model, true)
                                        }
                                    />
                                ) : (
                                    <OwnKeyModelSelect
                                        component={credentialComponent}
                                        provider={currentProvider}
                                        value={(watch(`${service}_model`) as string) || ""}
                                        onChange={(model) =>
                                            chooseModel(currentProvider, model, false)
                                        }
                                    />
                                )
                            ) : (
                                renderField(service, configFields[0], providerSchema)
                            )}
                        </div>
                    )}
                </div>

                {catalogueMode && !legacyManaged && (
                    <p className="text-xs text-muted-foreground">
                        {runsOnOurKey ? (
                            <>
                                Decibyl provides this model and bills it to your account —
                                no key needed. The price is per minute of this slot; the
                                bar below prices the whole call.
                            </>
                        ) : (
                            <>
                                This runs on your stored {getProviderDisplayName(currentProvider, providerSchema)} key
                                and is billed to you by them.{" "}
                                <a href="/integrations" className="underline">Manage keys</a>
                            </>
                        )}
                    </p>
                )}

                {/* Everything past the model, folded away.
                    These are the knobs that decide how natural an agent sounds
                    — speed, temperature, the turn-taking timings — and most of
                    them are right at their default. Laid out flat they made
                    every slot look like a form to fill in before the agent
                    would work, which is what a first-time buyer reads a screen
                    of empty-looking inputs as. Folded, the common path is a
                    vendor and a model; the knobs are one click away for the
                    person who came looking for them. Open by default when the
                    account has already set one, so a non-default setting is
                    never hidden behind a click nobody knew to make. */}
                {currentProvider && providerSchema && configFields.length > 1 && (
                    <Collapsible
                        open={advancedOpen[service] ?? hasNonDefaultFields(service, providerSchema)}
                        onOpenChange={(open) =>
                            setAdvancedOpen((prev) => ({ ...prev, [service]: open }))
                        }
                    >
                        <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                            <ChevronRight className="h-3.5 w-3.5 transition-transform data-[state=open]:rotate-90" />
                            Advanced settings
                            <span className="text-xs font-normal">
                                ({configFields.length - 1})
                            </span>
                        </CollapsibleTrigger>
                        <CollapsibleContent>
                            <div className="grid grid-cols-2 gap-4 pt-2">
                                {configFields.slice(1).map((field) => {
                                    const fieldSchema = providerSchema.properties[field];
                                    const actualFieldSchema = fieldSchema?.$ref && providerSchema.$defs
                                        ? providerSchema.$defs[fieldSchema.$ref.split('/').pop() || '']
                                        : fieldSchema;
                                    const fullWidth = actualFieldSchema?.multiline;
                                    const fallback = actualFieldSchema?.default;
                                    return (
                                        <div key={field} className={`space-y-2 ${fullWidth ? "col-span-2" : ""}`}>
                                            <Label className="flex items-baseline gap-2 capitalize">
                                                {field.replace(/_/g, ' ')}
                                                {/* What happens if you leave it
                                                    alone. Without it, "0.7" in a
                                                    box is a number somebody has to
                                                    decide about; with it, it is
                                                    visibly the setting we chose. */}
                                                {fallback !== undefined && fallback !== null && (
                                                    <span className="text-[11px] font-normal normal-case text-muted-foreground">
                                                        default {String(fallback)}
                                                    </span>
                                                )}
                                            </Label>
                                            {renderField(service, field, providerSchema)}
                                        </div>
                                    );
                                })}
                            </div>
                        </CollapsibleContent>
                    </Collapsible>
                )}

                {/* Keys do not belong on this screen. They live in the vault at
                    /provider-keys, and are resolved at dial time — so all this
                    needs to say is whether the one this slot depends on is
                    actually there. Showing an input here is what made every
                    model screen a key-entry screen, and what discarded a pasted
                    key when you changed provider. */}
                {keysFromVault && !managed && !(catalogueMode && runsOnOurKey) && currentProvider && providerSchema?.properties.api_key && (
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

        // A number the schema has bounded is a range, so show it as one. The
        // provider classes carry the real limits, so this needs no per-provider
        // knowledge here: ElevenLabs stability, Cartesia speed and Sarvam speed
        // all arrive with their own min, max and default already attached.
        const lower = actualSchema?.minimum ?? actualSchema?.exclusiveMinimum;
        const upper = actualSchema?.maximum ?? actualSchema?.exclusiveMaximum;
        // A bounded number is a slider only while dragging is the easier way to
        // set it. max_tokens runs 16-4096: as a track that is four thousand
        // indistinguishable positions, and nobody wants "about 250" — they want
        // 250. Wide ranges stay a box, which is what Vapi shows for that field
        // and a slider for temperature, for the same reason.
        const sliderStops = typeof lower === "number" && typeof upper === "number"
            ? upper - lower
            : 0;
        if (
            actualSchema?.type === "number"
            && typeof lower === "number"
            && typeof upper === "number"
            && sliderStops <= 100
        ) {
            const fieldKey = `${service}_${field}`;
            const span = upper - lower;
            // Integer-looking ranges step by 1; everything else gets a tidy
            // 0.1/0.05/0.01 rather than an arbitrary fraction of the span.
            const step = Number.isInteger(lower)
                && Number.isInteger(upper)
                && Number.isInteger(actualSchema.default ?? 0)
                && span >= 4
                ? 1
                : span > 5 ? 0.1 : span >= 1 ? 0.05 : 0.01;
            // An exclusive bound excludes its own value, so start one step in:
            // a slider that can be dragged to a number the server rejects is
            // worse than one that cannot reach it.
            const min = actualSchema.minimum === undefined ? lower + step : lower;
            const max = actualSchema.maximum === undefined ? upper - step : upper;
            const fallback = typeof actualSchema.default === "number"
                ? actualSchema.default
                : min;
            const current = Number(watch(fieldKey) ?? fallback);

            return (
                <Slider
                    id={fieldKey}
                    min={min}
                    max={max}
                    step={step}
                    value={Number.isFinite(current) ? current : fallback}
                    onValueChange={(next) =>
                        setValue(fieldKey, next, { shouldDirty: true })
                    }
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
                carriageNote={carriageNote}
                stack={pricedStack(
                    {
                        stt: { provider: serviceProviders.stt, model: watch("stt_model") as string },
                        llm: { provider: serviceProviders.llm, model: watch("llm_model") as string },
                        tts: { provider: serviceProviders.tts, model: watch("tts_model") as string },
                        is_realtime: isRealtime,
                    },
                    managedUpstream,
                    telephonyProvider,
                )}
            />

            <Card>
                <CardContent className="pt-6">
                    <Tabs key={defaultTab} defaultValue={defaultTab} className="w-full">
                        {/* Each tab carries its own current setting.

                            Three of the four choices are hidden at any moment
                            behind a tab strip that said only "LLM", "Voice",
                            "Transcriber" — so reading the stack you have
                            configured meant clicking through it and
                            remembering. The slot name is the heading and what
                            it resolves to is the line under it, which makes the
                            whole pipeline legible without opening anything. */}
                        <TabsList className="mb-6 flex h-auto w-full items-stretch gap-4 bg-transparent p-0">
                            {(["voice", "model"] as TabGroup[]).map((group) => {
                                const inGroup = visibleTabs.filter((t) => t.group === group);
                                if (inGroup.length === 0) return null;
                                return (
                                    <div key={group} className="flex-1 space-y-1.5">
                                        <span className="block px-1 text-[11px] font-medium uppercase tracking-[0.06em] text-muted-foreground">
                                            {TAB_GROUP_LABELS[group]}
                                        </span>
                                        <div
                                            className="grid gap-1 rounded-lg bg-muted p-1"
                                            style={{
                                                gridTemplateColumns: `repeat(${inGroup.length}, 1fr)`,
                                            }}
                                        >
                                            {inGroup.map(({ key, label }) => {
                                                const provider = serviceProviders[key];
                                                const model = watch(`${key}_model`) as
                                                    | string
                                                    | undefined;
                                                const managed = provider === "decibyl";
                                                return (
                                                    <TabsTrigger
                                                        key={key}
                                                        value={key}
                                                        className="flex-col items-start gap-0.5 px-3 py-2 text-left"
                                                    >
                                                        <span className="text-[0.9375rem] font-semibold tracking-[-0.015em]">
                                                            {label}
                                                        </span>
                                                        <span className="w-full truncate text-[11px] font-normal leading-tight text-muted-foreground">
                                                            {provider
                                                                ? managed
                                                                    ? `Decibyl · ${model || "default"}`
                                                                    : model || provider
                                                                : "Not set"}
                                                        </span>
                                                    </TabsTrigger>
                                                );
                                            })}
                                        </div>
                                    </div>
                                );
                            })}
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
