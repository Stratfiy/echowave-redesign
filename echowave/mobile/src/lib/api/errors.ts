/**
 * Turning an API error into words a person can read.
 *
 * The generated client resolves to `{ data, error, response }` and never
 * throws on a 4xx/5xx (ui/AGENTS.md, "API Error Handling"). FastAPI shapes
 * `detail` as a string, as an array of `{ msg, loc }` on a 422, or as an
 * object (`{ message, stored }` on a conflict, `{ code, message }` from
 * voice). Rendering any of those raw crashes a Text node or shows JSON, so
 * every screen goes through `detailFromError`.
 */

export class ApiError extends Error {
    readonly status: number;
    readonly detail: unknown;
    readonly headers: Record<string, string>;

    constructor(status: number, detail: unknown, message: string, headers: Record<string, string> = {}) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.detail = detail;
        this.headers = headers;
    }

    /** `detail.code` when the API sent a machine code (voice, helpers). */
    get code(): string | undefined {
        const d = this.detail as { code?: unknown } | null;
        return d && typeof d === 'object' && typeof d.code === 'string' ? d.code : undefined;
    }
}

function fieldFromLoc(loc: unknown): string | null {
    if (!Array.isArray(loc)) return null;
    const parts = loc
        .filter((part): part is string => typeof part === 'string')
        .filter((part) => !['body', 'query', 'path', 'header', 'cookie'].includes(part));
    return parts.length > 0 ? parts.join('.') : null;
}

export function detailFromError(err: unknown, fallback = 'Something went wrong. Try again.'): string {
    if (err == null) return fallback;
    if (typeof err === 'string') return err || fallback;
    if (err instanceof ApiError) return detailFromError({ detail: err.detail }, err.message || fallback);
    if (err instanceof Error) return err.message || fallback;
    const detail = (err as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
    if (Array.isArray(detail)) {
        const lines = detail
            .map((item) => {
                if (!item || typeof item !== 'object') return null;
                const i = item as { msg?: unknown; message?: unknown; loc?: unknown };
                const text = typeof i.msg === 'string' ? i.msg : typeof i.message === 'string' ? i.message : null;
                if (!text) return null;
                const field = fieldFromLoc(i.loc);
                return field ? `${field}: ${text}` : text;
            })
            .filter((line): line is string => Boolean(line));
        if (lines.length) return lines.join('\n');
    }
    if (detail && typeof detail === 'object') {
        const d = detail as { message?: unknown };
        if (typeof d.message === 'string' && d.message) return d.message;
    }
    const message = (err as { message?: unknown }).message;
    if (typeof message === 'string' && message) return message;
    return fallback;
}
