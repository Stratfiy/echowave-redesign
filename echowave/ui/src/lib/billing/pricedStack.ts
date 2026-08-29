import type { CostStack } from "@/components/CostPerMinuteBar";

/** One slot of a model configuration, however it was chosen. */
export interface ModelSlot {
    provider?: string | null;
    model?: string | null;
}

/** The parts of a resolved model configuration that carry a price. */
export interface PriceableConfiguration {
    llm?: ModelSlot | null;
    stt?: ModelSlot | null;
    tts?: ModelSlot | null;
    is_realtime?: boolean | null;
}

/**
 * Turn a chosen or resolved model configuration into a stack the estimator can price.
 *
 * Two translations, both of which are wrong to skip:
 *
 * A managed slot says `provider: "decibyl"`, which is a billing arrangement
 * rather than a vendor. The estimator is keyed by real provider and model, so
 * passing it through reports the managed option as the one we cannot cost —
 * the opposite of true. `managedUpstream` is what those tiers resolve to today.
 *
 * A speech-to-speech configuration has no separate transcriber or voice: one
 * model hears and speaks, and pricing the empty slots beside it would count
 * components that never run.
 */
export function pricedStack(
    configuration: PriceableConfiguration | null | undefined,
    managedUpstream?: Record<string, { provider: string; model: string }>,
    telephonyProvider?: string | null,
): CostStack {
    const isRealtime = Boolean(configuration?.is_realtime);

    const slot = (name: "stt" | "llm" | "tts", value: ModelSlot | null | undefined) => {
        const upstream = value?.provider === "decibyl" ? managedUpstream?.[name] : undefined;
        return {
            [`${name}_provider`]: upstream?.provider ?? value?.provider ?? null,
            [`${name}_model`]: upstream?.model ?? value?.model ?? "",
        };
    };

    return {
        ...slot("stt", isRealtime ? null : configuration?.stt),
        ...slot("llm", configuration?.llm),
        ...slot("tts", isRealtime ? null : configuration?.tts),
        telephony_provider: telephonyProvider ?? null,
    };
}

/** The saved override's shape, narrowed to what pricing needs. */
interface V2Configuration {
    mode?: "decibyl" | "byok" | string;
    decibyl?: { llm_tier?: string; stt_tier?: string; tts_tier?: string } | null;
    byok?: {
        mode?: "pipeline" | "realtime" | string;
        pipeline?: { llm?: ModelSlot; stt?: ModelSlot; tts?: ModelSlot } | null;
        realtime?: { llm?: ModelSlot } | null;
    } | null;
}

/**
 * Flatten a saved model configuration into the slots that carry a price.
 *
 * The stored shape nests by how the models were *chosen* — managed tiers,
 * bring-your-own-key, speech-to-speech — while a price only depends on what
 * ends up running. This is the UI-side counterpart of
 * `compile_ai_model_configuration_v2`, reduced to the fields pricing reads.
 */
export function priceableFromV2(
    configuration: V2Configuration | null | undefined,
): PriceableConfiguration | null {
    if (!configuration) return null;

    if (configuration.mode === "decibyl") {
        const managed = configuration.decibyl ?? {};
        // A tier is not a model, but `pricedStack` resolves the "decibyl"
        // provider through the upstream map, which is where the real one is.
        return {
            llm: { provider: "decibyl", model: managed.llm_tier ?? "" },
            stt: { provider: "decibyl", model: managed.stt_tier ?? "" },
            tts: { provider: "decibyl", model: managed.tts_tier ?? "" },
            is_realtime: false,
        };
    }

    const byok = configuration.byok;
    if (byok?.mode === "realtime") {
        return { llm: byok.realtime?.llm ?? null, is_realtime: true };
    }

    return {
        llm: byok?.pipeline?.llm ?? null,
        stt: byok?.pipeline?.stt ?? null,
        tts: byok?.pipeline?.tts ?? null,
        is_realtime: false,
    };
}
