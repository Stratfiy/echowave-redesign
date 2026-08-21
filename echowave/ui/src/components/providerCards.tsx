/**
 * Shared between the customer and superadmin provider-keys screens.
 *
 * Both draw the same idea — one card per vendor, filterable by what it
 * serves, "Connect" rather than "fill in three forms" — because the registry
 * behind them is the same one. Kept in one place so a vendor's display name or
 * the set of filterable components cannot read differently on the two
 * screens; two copies of this list are two places to forget to update when a
 * vendor is added.
 */

import type { ReactNode } from "react";

/**
 * The slots a key can serve. Telephony is deliberately absent: carrier
 * credentials live on a telephony configuration, which also models the KYC
 * that comes with a phone number.
 *
 * These are categories for *reading* — a filter over one list of vendors, not
 * lists to fill in one at a time. A customer with one Sarvam account meeting
 * their one Sarvam key three times reasonably concluded the product wanted
 * three different things; it wanted one.
 *
 * "realtime" never gets a key of its own: a speech-to-speech vendor
 * authenticates with its ordinary sibling's key (OpenAI Realtime bills to
 * your OpenAI key), so it appears here as one more thing a vendor's key
 * covers — see ``registry.REALTIME_KEY_PROVIDER`` on the backend, the single
 * source of truth this mirrors.
 */
export const COMPONENTS = [
    { value: "stt", title: "Transcription", noun: "transcription" },
    { value: "llm", title: "Model", noun: "model" },
    { value: "tts", title: "Voice", noun: "voice" },
    { value: "realtime", title: "Realtime", noun: "realtime" },
] as const;

export type ComponentValue = (typeof COMPONENTS)[number]["value"];

export function componentNoun(component: string): string {
    return COMPONENTS.find((c) => c.value === component)?.noun ?? component;
}

/** "transcription, model and voice" — the vendor's coverage, said once. */
export function describeComponents(components: readonly string[]): string {
    const nouns = components.map(componentNoun);
    if (nouns.length === 0) return "";
    if (nouns.length === 1) return nouns[0];
    return `${nouns.slice(0, -1).join(", ")} and ${nouns[nouns.length - 1]}`;
}

/**
 * The components among ``serves`` that a credential row can actually be
 * stored against. Realtime is never one of them — a speech-to-speech vendor
 * authenticates with its ordinary sibling's key rather than a row of its
 * own — so a vendor whose key covers every *keyable* slot should not read as
 * partially connected just because "realtime" is also in ``serves``.
 */
export function keyableComponents(
    serves: readonly ComponentValue[],
): ComponentValue[] {
    return serves.filter((component) => component !== "realtime");
}

// Vendors capitalise their own names, and title-casing produces "Openai" and
// "Elevenlabs" — which look like we do not know who we are integrating with.
// Only the ones whose casing a naive transform gets wrong, or whose registry
// key reads worse than a familiar product name, are listed; anything missing
// falls back to title case, so a new provider still reads sensibly with no
// change here.
const PROVIDER_LABELS: Record<string, string> = {
    openai: "OpenAI",
    openai_realtime: "OpenAI Realtime",
    google: "Google (Gemini)",
    google_vertex: "Google Vertex",
    google_realtime: "Google Realtime",
    azure: "Azure OpenAI",
    azure_speech: "Azure Speech",
    azure_realtime: "Azure Realtime",
    aws_bedrock: "AWS Bedrock",
    elevenlabs: "ElevenLabs",
    openrouter: "OpenRouter",
    huggingface: "Hugging Face",
    minimax: "MiniMax",
    xai: "xAI",
    assemblyai: "AssemblyAI",
    grok_realtime: "Grok Realtime",
    ultravox_realtime: "Ultravox Realtime",
};

export function providerLabel(provider: string): string {
    return (
        PROVIDER_LABELS[provider] ??
        provider
            .split("_")
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(" ")
    );
}

/** One chip in the "All providers / Transcription / Model / Voice" filter row. */
export function FilterChip({
    active,
    onClick,
    children,
}: {
    active: boolean;
    onClick: () => void;
    children: ReactNode;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={
                active
                    ? "rounded-full border border-transparent bg-[color:var(--primary)] px-3 py-1.5 text-sm font-medium text-[color:var(--primary-foreground)]"
                    : "rounded-full border border-border bg-background px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted"
            }
        >
            {children}
        </button>
    );
}
