/**
 * Figures for the staff console. A missing value is an em dash, never 0;
 * money keeps its exact minor units and its currency on every value.
 */

export const DASH = "—";

export function money(minor: number | null | undefined, currency = "INR"): string {
    if (minor === null || minor === undefined) return DASH;
    const major = minor / 100;
    try {
        return new Intl.NumberFormat("en-IN", {
            style: "currency",
            currency,
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(major);
    } catch {
        return `${currency} ${major.toFixed(2)}`;
    }
}

export function percent(ratio: number | null | undefined): string {
    if (ratio === null || ratio === undefined) return DASH;
    return `${(ratio * 100).toFixed(1)}%`;
}

export function count(value: number | null | undefined): string {
    if (value === null || value === undefined) return DASH;
    return new Intl.NumberFormat("en-IN").format(value);
}

export function when(iso: string | null | undefined): string {
    if (!iso) return DASH;
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });
}

export function ago(date: Date | null, now: Date = new Date()): string {
    if (!date) return "not yet";
    const seconds = Math.max(0, Math.round((now.getTime() - date.getTime()) / 1000));
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    return `${Math.round(minutes / 60)} h ago`;
}

/** A state code ("outcome_unknown") as words ("Outcome unknown"). */
export function words(code: string | null | undefined): string {
    if (!code) return DASH;
    const text = code.replace(/[_.]/g, " ");
    return text.charAt(0).toUpperCase() + text.slice(1);
}

/** An idempotency key that stays the same for one preview, so a double
 *  press or a retry is the same request. */
export function newIdempotencyKey(prefix = "staff"): string {
    const random =
        typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${random}`;
}

/** "#42 refund.request [queued] ..." -> 42: the staff command an audit row
 *  is about, so request, approval and result read as one action. */
export function commandOf(note: string | null | undefined): number | null {
    const match = note?.match(/^#(\d+) /);
    return match ? Number(match[1]) : null;
}
