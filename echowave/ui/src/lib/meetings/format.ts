/** Small, pure helpers the meeting screens share. */

/** 0:05:07 for capture time; hours always shown so the width is stable. */
export function clock(ms: number): string {
    const seconds = Math.max(0, Math.floor((ms || 0) / 1000));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor(seconds / 60) % 60;
    const s = seconds % 60;
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** "4 min" or "1 h 5 min" for a duration line. */
export function duration(ms: number): string {
    const minutes = Math.round((ms || 0) / 60000);
    if (minutes < 1) return "under a minute";
    if (minutes < 60) return `${minutes} min`;
    return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** A due time with its full date, never "tomorrow" (handoff 22). */
export function fullDate(iso: string | null | undefined): string | null {
    if (!iso) return null;
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return null;
    return when.toLocaleString(undefined, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

/** What a meeting's status means, in words, with its tone. */
export function statusLine(status: string): { label: string; tone: "ok" | "warn" | "error" | "busy" } {
    switch (status) {
        case "recording":
            return { label: "Recording", tone: "busy" };
        case "paused":
            return { label: "Paused", tone: "warn" };
        case "uploading":
            return { label: "Waiting for the recording", tone: "busy" };
        case "processing":
            return { label: "Processing", tone: "busy" };
        case "ready":
            return { label: "Ready", tone: "ok" };
        case "partial":
            return { label: "Partial", tone: "warn" };
        case "failed":
            return { label: "Failed", tone: "error" };
        default:
            return { label: status, tone: "warn" };
    }
}

/** Where a card is, in the words its row shows. */
export function cardLine(state: string | null | undefined): string {
    switch (state) {
        case "proposed":
            return "Waiting for your approval";
        case "armed":
            return "Approved. It runs in a few seconds; you can still undo.";
        case "running":
            return "Adding it now";
        case "done":
            return "Added to the task board";
        case "failed":
            return "Not added";
        case "declined":
            return "Not done";
        case "cancelled":
        case "undone":
            return "Taken back";
        case "outcome_unknown":
            return "We are checking whether this was added. Please do not add it again.";
        default:
            return "Suggested";
    }
}

/** A datetime-local value (no zone) for an ISO time, in local time. */
export function toLocalInput(iso: string | null | undefined): string {
    if (!iso) return "";
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return "";
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

/** The ISO time for a datetime-local value, read in local time. */
export function fromLocalInput(value: string): string | null {
    if (!value) return null;
    const when = new Date(value);
    return Number.isNaN(when.getTime()) ? null : when.toISOString();
}
