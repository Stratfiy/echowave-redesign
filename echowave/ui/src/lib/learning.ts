/**
 * Small shared pieces for the Learning Guide screens (launch stream
 * `learning`; screens 13-14). The API is api/routes/learning.py; every
 * refusal there carries `{code, message}` so a screen can act on the code
 * and show the message as written.
 */

export type LearningRefusal = {
    code: string;
    message: string;
    categories?: string[];
    stored?: Record<string, unknown>;
};

const FALLBACK: LearningRefusal = {
    code: "failed",
    message: "That did not go through. Try again.",
};

/** The refusal in an API error, or a plain failure when there is none. */
export function refusalOf(error: unknown): LearningRefusal {
    if (!error || typeof error !== "object") return FALLBACK;
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && "code" in detail && "message" in detail) {
        return detail as LearningRefusal;
    }
    if (typeof detail === "string") return { code: "refused", message: detail };
    return FALLBACK;
}

/** One key per answer being submitted: a retry of the same answer reuses
 *  it, so the server marks it once however many times it is sent. */
export function newAttemptKey(): string {
    try {
        return crypto.randomUUID();
    } catch {
        return `k-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
}

/** Plain labels for a marking (screen 14: "Practised", "Needs another
 *  attempt"; never a score). */
export const OUTCOME_LABEL: Record<string, string> = {
    passed: "Correct",
    partly: "Partly there",
    not_yet: "Not yet",
};

/** Native names beside English (screen 02's rule for language lists). */
export const LANGUAGE_NAMES: Record<string, string> = {
    "en-IN": "English (India)",
    en: "English",
    "hi-IN": "हिन्दी · Hindi",
    "bn-IN": "বাংলা · Bengali",
    "ta-IN": "தமிழ் · Tamil",
    "te-IN": "తెలుగు · Telugu",
    "kn-IN": "ಕನ್ನಡ · Kannada",
    "ml-IN": "മലയാളം · Malayalam",
    "mr-IN": "मराठी · Marathi",
    "gu-IN": "ગુજરાતી · Gujarati",
    "pa-IN": "ਪੰਜਾਬੀ · Punjabi",
    "od-IN": "ଓଡ଼ିଆ · Odia",
};

export function languageName(tag: string | null | undefined): string {
    if (!tag) return "";
    return LANGUAGE_NAMES[tag] ?? tag;
}

/** Where "Continue practice" opens: the lesson inside Chat. */
export function resumeHref(goalId: string, reviewSkillId?: number): string {
    const params = new URLSearchParams({ learn: goalId });
    if (reviewSkillId != null) params.set("review", String(reviewSkillId));
    return `/overview?${params.toString()}`;
}

export function formatDay(iso: string | null | undefined): string {
    if (!iso) return "";
    try {
        return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
    } catch {
        return "";
    }
}
