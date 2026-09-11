/**
 * What to call a vendor on screen.
 *
 * Vendor ids are lowercase machine strings, and two of them are the same
 * company twice — `openai` and `openai_realtime` are both OpenAI, and a picker
 * that groups models by vendor has to group those together or the list has two
 * OpenAIs in it.
 *
 * Lives on its own because the tiles and the picker behind their pencil both
 * need it, and the picker is imported by the tiles.
 */

const VENDOR_NAMES: Record<string, string> = {
    openai: "OpenAI",
    openai_realtime: "OpenAI",
    google: "Google",
    google_realtime: "Google",
    google_vertex: "Google Vertex",
    sarvam: "Sarvam",
    deepgram: "Deepgram",
    elevenlabs: "ElevenLabs",
    anthropic: "Anthropic",
    cartesia: "Cartesia",
    azure: "Azure",
    groq: "Groq",
    rumik: "Rumik",
    smallest: "Smallest",
    decibyl: "Decibyl",
};

export function vendorName(provider: string): string {
    return VENDOR_NAMES[provider] ?? provider;
}
