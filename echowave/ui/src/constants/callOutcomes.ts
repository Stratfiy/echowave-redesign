/**
 * What a call achieved, as a label somebody can sort a hundred calls by.
 *
 * Not `DISPOSITION_CODES` in `dispositionCodes.ts`. Those are how a call
 * *ended* — `user_hangup`, `voicemail_detected` — and a call is legitimately
 * both `user_hangup` and `booked`: the customer got what they wanted and put
 * the phone down. Keeping the two in one field would force a choice between
 * two facts that are both true, so they stay separate fields with separate
 * filters.
 *
 * This list mirrors `DEFAULT_DISPOSITIONS` in
 * `api/services/workflow/disposition.py`. It is what an agent classifies
 * against until somebody edits it, and what the editor offers as a starting
 * point — a business changes it rather than being handed a taxonomy.
 */
export type CallOutcome = {
    /** Stored, and narrow: it ends up a CRM field name and a CSV heading. */
    code: string;
    /** Shown. */
    label: string;
    /** What the classifier is told this code means. */
    when: string;
};

/** Always available and never removable. See `UNCLEAR` in the backend. */
export const UNCLEAR_OUTCOME: CallOutcome = {
    code: "unclear",
    label: "Unclear",
    when: "The conversation did not settle either way",
};

export const DEFAULT_CALL_OUTCOMES: readonly CallOutcome[] = [
    { code: "booked", label: "Booked", when: "An appointment, demo or visit is confirmed" },
    { code: "interested", label: "Interested", when: "Wants it, but committed to nothing yet" },
    { code: "callback", label: "Call back", when: "Asked to be contacted at another time" },
    { code: "not_interested", label: "Not interested", when: "A clear no" },
    { code: "wrong_number", label: "Wrong number", when: "Not the person we were trying to reach" },
    { code: "no_answer", label: "No answer", when: "Nobody picked up, or picked up and said nothing" },
    { code: "unreachable", label: "Unreachable", when: "Invalid, switched off, or could not connect" },
    UNCLEAR_OUTCOME,
] as const;

/** More than this is not a taxonomy anybody sorts by, and each one costs
 * prompt tokens on every call. Mirrors `MAX_DISPOSITIONS`. */
export const MAX_CALL_OUTCOMES = 30;

/**
 * Coerce what somebody typed into a storable code, or reject it.
 *
 * Mirrors `normalise_code` in the backend, which is the one that decides —
 * this exists so the editor can show the stored form as it is typed rather
 * than silently rewriting it on save.
 */
export function normaliseOutcomeCode(raw: string): string | null {
    const candidate = raw
        .trim()
        .toLowerCase()
        .replace(/[\s-]/g, "_")
        .replace(/[^a-z0-9_]/g, "")
        .slice(0, 40);
    return /^[a-z][a-z0-9_]{0,39}$/.test(candidate) ? candidate : null;
}

/**
 * The list to save, with `unclear` guaranteed present.
 *
 * Without it the classifier has nowhere to put a call it genuinely cannot
 * read, and picks the nearest real outcome instead — which is how a bad line
 * ends up in somebody's report as a "not interested".
 *
 * Rows with no code are how somebody leaves the editor mid-thought, not
 * something to store.
 */
export function outcomesToSave(rows: CallOutcome[]): CallOutcome[] {
    const kept: CallOutcome[] = [];
    const seen = new Set<string>();
    for (const row of rows) {
        const code = normaliseOutcomeCode(row.code);
        if (!code || seen.has(code)) continue;
        seen.add(code);
        kept.push({
            code,
            label: row.label.trim() || code.replace(/_/g, " "),
            when: row.when.trim(),
        });
    }
    if (kept.length === 0) return [];
    if (!seen.has(UNCLEAR_OUTCOME.code)) kept.push({ ...UNCLEAR_OUTCOME });
    return kept.slice(0, MAX_CALL_OUTCOMES + 1);
}
