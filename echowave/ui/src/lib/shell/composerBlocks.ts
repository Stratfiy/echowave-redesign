/**
 * Blocks that ride with a Chat message: pasted notes and voice notes. They
 * are kept apart from the typed question on screen and joined into the one
 * message the server receives, labelled, so Decibyl can tell "here is my
 * question" from "here is the material".
 */

export type ComposerBlock = {
    id: string;
    kind: "notes" | "voice";
    text: string;
    /** For a voice note, how long it was, in seconds. */
    seconds?: number;
};

export function blockLabel(block: ComposerBlock): string {
    if (block.kind === "voice") {
        const secs = Math.max(0, Math.round(block.seconds ?? 0));
        const clock = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
        return `Voice note (${clock})`;
    }
    const words = block.text.trim().split(/\s+/).filter(Boolean).length;
    return `Notes (${words} ${words === 1 ? "word" : "words"})`;
}

/** The message as sent: the question, then each block under its label. */
export function composeMessage(text: string, blocks: readonly ComposerBlock[]): string {
    const parts = [text.trim()];
    for (const block of blocks) {
        const heading = block.kind === "voice" ? `${blockLabel(block)}, transcribed:` : "Notes:";
        parts.push(`${heading}\n${block.text.trim()}`);
    }
    return parts.filter(Boolean).join("\n\n");
}
