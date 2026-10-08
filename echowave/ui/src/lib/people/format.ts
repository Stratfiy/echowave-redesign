/**
 * Small words for the People screen (PEOPLE.md): how long ago, which source,
 * which channel. Kept apart so the list and the person page say the same.
 */

export const SOURCE_LABELS: Record<string, string> = {
    google: "Google",
    microsoft: "Outlook",
    vcard: "vCard",
    csv: "CSV",
    picker: "Phone",
    manual: "Added by you",
    decibyl: "From Decibyl",
};

export const CHANNEL_LABELS: Record<string, string> = {
    call: "Call",
    whatsapp: "WhatsApp",
    email: "Email",
    meeting: "Meeting",
};

export function sourceLabel(source: string): string {
    return SOURCE_LABELS[source] ?? source;
}

export function channelLabel(channel: string): string {
    // A channel added later is still shown, under its own name.
    return CHANNEL_LABELS[channel] ?? channel;
}

export function ago(iso: string | null | undefined, now: Date = new Date()): string {
    if (!iso) return "";
    const then = new Date(iso);
    const seconds = Math.max(0, Math.round((now.getTime() - then.getTime()) / 1000));
    if (seconds < 60) return "just now";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return days === 1 ? "yesterday" : `${days} days ago`;
    return then.toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
    });
}

export function syncedLine(counts: Record<string, number> | undefined): string {
    if (!counts) return "";
    const parts: string[] = [];
    if (counts.added) parts.push(`${counts.added} added`);
    if (counts.updated) parts.push(`${counts.updated} updated`);
    if (counts.removed) parts.push(`${counts.removed} removed`);
    return parts.length ? parts.join(", ") : "no changes";
}
