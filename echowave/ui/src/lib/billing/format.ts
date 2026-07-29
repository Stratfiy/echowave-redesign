/**
 * Display formatting for billing figures.
 *
 * Money crosses the wire as integer paise and is only ever turned into rupees
 * here, at the edge. Nothing in the UI does arithmetic on these strings, and
 * nothing persists them.
 */

/** ₹1 = 100 paise. */
const PAISE_PER_RUPEE = 100;
/** 1 paise = 1000 millipaise — the unit rates are quoted in. */
const MPAISE_PER_PAISE = 1000;

/**
 * Indian digit grouping (1,23,456 rather than 123,456). The audience for this
 * dashboard reads rupees in lakhs and crores.
 */
const rupees = new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

/**
 * Explicit Indian abbreviations rather than `notation: "compact"`.
 *
 * Chrome's en-IN compact data renders a thousand as "1T", which reads as
 * "trillion" to most people and is exactly the wrong thing on a revenue axis.
 * Node's ICU renders the same value as "1K", so the ambiguity is also
 * environment-dependent. Spelling the thresholds out keeps the axis honest
 * everywhere.
 */
function abbreviateRupees(rupees: number): string {
    const abs = Math.abs(rupees);
    const sign = rupees < 0 ? "-" : "";
    const trim = (n: number) => Number(n.toFixed(1)).toString();

    if (abs >= 10_000_000) return `${sign}₹${trim(abs / 10_000_000)}Cr`;
    if (abs >= 100_000) return `${sign}₹${trim(abs / 100_000)}L`;
    if (abs >= 1_000) return `${sign}₹${trim(abs / 1_000)}k`;
    return `${sign}₹${Math.round(abs)}`;
}

const integers = new Intl.NumberFormat("en-IN");

/** Full precision, e.g. ₹1,23,456.78. Use in tables and receipts. */
export function formatPaise(paise: number | null | undefined): string {
    if (paise === null || paise === undefined) return "—";
    return rupees.format(paise / PAISE_PER_RUPEE);
}

/** Abbreviated, e.g. ₹1.2L. Use on stat tiles and axis ticks where space is tight. */
export function formatPaiseCompact(paise: number | null | undefined): string {
    if (paise === null || paise === undefined) return "—";
    return abbreviateRupees(paise / PAISE_PER_RUPEE);
}

/** A per-minute unit rate held in millipaise, e.g. 200000 → "₹2.00". */
export function formatRateMpaise(mpaise: number | null | undefined): string {
    if (mpaise === null || mpaise === undefined) return "—";
    return rupees.format(mpaise / MPAISE_PER_PAISE / PAISE_PER_RUPEE);
}

export function formatNumber(value: number | null | undefined): string {
    if (value === null || value === undefined) return "—";
    return integers.format(value);
}

/**
 * A ratio as a percentage. `null` renders as an em dash rather than "0%":
 * margin on zero revenue is undefined, and "0%" would read as "no margin".
 */
export function formatPercent(
    ratio: number | null | undefined,
    fractionDigits = 1,
): string {
    if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
    return `${(ratio * 100).toFixed(fractionDigits)}%`;
}

export function formatMs(ms: number | null | undefined): string {
    if (ms === null || ms === undefined) return "—";
    if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
    return `${Math.round(ms)}ms`;
}

export function formatDuration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return "—";
    const mins = Math.floor(seconds / 60);
    const secs = Math.round(seconds % 60);
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
}

/** Timestamps are stored in UTC and always displayed in IST. */
export function formatDateTimeIST(iso: string | null | undefined): string {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
    });
}

export function formatDateIST(iso: string | null | undefined): string {
    if (!iso) return "—";
    // Date-only strings are already IST calendar days from the rollup; parsing
    // them as UTC and re-rendering in IST would shift them a day.
    const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-IN", {
        timeZone: "UTC",
        day: "numeric",
        month: "short",
    });
}

/** Percentage change between two periods, or null when the base is zero. */
export function changeRatio(
    current: number | null | undefined,
    previous: number | null | undefined,
): number | null {
    if (!previous || current === null || current === undefined) return null;
    return (current - previous) / previous;
}

export const ISO_TODAY_IST = (): string =>
    new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });

export function isoDaysAgoIST(days: number): string {
    const now = new Date();
    now.setUTCDate(now.getUTCDate() - days);
    return now.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
}
