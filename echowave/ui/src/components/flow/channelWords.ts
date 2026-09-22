/**
 * What a node type is called on the canvas, in the language of the channel
 * the bot is on.
 *
 * The node types are call-named — `startCall`, `endCall`, `qa` — because
 * calls were all there was when they were made, and the spec catalogue the
 * API serves carries those words as display names for every bot. A scheduled
 * email agent therefore opened reading Start Call → … → End Call, with a
 * "QA Analysis" beside it (22 September 2026, the pilot's Outbound
 * Prospecting agent). The types are the definition format and stay; the
 * words are the renderer's, and the renderer knows the channel.
 *
 * Voice keeps the spec's own display name untouched. Chat renames only the
 * types whose spec name talks about a call; anything else — Webhook, an
 * integration — is already channel-neutral and passes through.
 */

export type BotChannel = "voice" | "chat";

const CHAT_WORDS: Readonly<Record<string, string>> = {
    startCall: "Start",
    agentNode: "Step",
    endCall: "Finish",
    qa: "Review",
    globalNode: "Persona",
};

/** The subtitle under a node's name: its kind, in the channel's words. */
export function nodeKindLabel(
    specName: string | undefined,
    specDisplayName: string | undefined,
    channel: BotChannel | null | undefined,
): string {
    const fallback = specDisplayName ?? "Node";
    if (channel !== "chat" || !specName) return fallback;
    return CHAT_WORDS[specName] ?? fallback;
}

/** Which channel a stored configuration block describes; absent is voice,
 * for the same reason the API reads it that way — every bot that existed
 * before the field did was a call bot. */
export function channelOf(configurations: object | null | undefined): BotChannel {
    const raw = (configurations as { channel?: unknown } | null | undefined)?.channel;
    return raw === "chat" ? "chat" : "voice";
}
