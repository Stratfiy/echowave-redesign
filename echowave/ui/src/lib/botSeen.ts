/**
 * When this person last opened each bot's chat, in this browser.
 *
 * Per viewer and per device on purpose, for now: the unread dot is a reading
 * aid, not a record, and a server-side watermark is a table and a migration
 * for a thing nobody has asked to be shared across devices yet. localStorage
 * can be absent or throw (private windows, cleared site data), so every read
 * and write is guarded and the page renders correctly without it.
 */

const KEY = "decibyl.bot-seen";

function readAll(): Record<string, string> {
    try {
        const raw = window.localStorage.getItem(KEY);
        const parsed = raw ? (JSON.parse(raw) as unknown) : null;
        return parsed && typeof parsed === "object" ? (parsed as Record<string, string>) : {};
    } catch {
        return {};
    }
}

/** ISO time this bot's chat was last opened here, or null. */
export function lastSeen(workflowId: number): string | null {
    return readAll()[String(workflowId)] ?? null;
}

/** Note that this bot's chat is being read now. Returns the previous mark. */
export function markSeen(workflowId: number, at: Date = new Date()): string | null {
    const all = readAll();
    const before = all[String(workflowId)] ?? null;
    all[String(workflowId)] = at.toISOString();
    try {
        window.localStorage.setItem(KEY, JSON.stringify(all));
    } catch {
        // Nothing to do: the dot will show again next time, which is the
        // conservative failure.
    }
    return before;
}

/** Whether something happened on this bot after it was last opened here. */
export function isUnread(workflowId: number, lastAt: string | null | undefined): boolean {
    if (!lastAt) return false;
    const seen = lastSeen(workflowId);
    if (!seen) return true;
    return new Date(lastAt).getTime() > new Date(seen).getTime();
}
