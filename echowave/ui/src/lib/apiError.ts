/**
 * Extract a human-readable message from a backend error response.
 *
 * The generated API client returns `{ error }` on failure (it does not throw),
 * and FastAPI shapes that error as `{ detail: string }`, `{ detail:
 * [{ msg, loc, ... }] }`, or backend validation arrays like `{ detail:
 * [{ model, message }] }`. This normalizes those to a single string so it can
 * be rendered or thrown directly.
 */

/** The field a 422 item is about, from FastAPI's `loc` tuple.
 *
 * `loc` reads like `["body", "legal_name"]` — the first element says where the
 * value came from, and the rest name the field. The wrapper elements are
 * dropped because "body" is not a field the person filling in the form has
 * heard of, and a numeric index is a position in an array they cannot see
 * either.
 */
function fieldFromLoc(loc: unknown): string | null {
    if (!Array.isArray(loc)) return null;
    const parts = loc
        .filter((part): part is string => typeof part === "string")
        .filter((part) => !["body", "query", "path", "header", "cookie"].includes(part));
    return parts.length > 0 ? parts.join(".") : null;
}

/** Field-keyed problems, as the KYC submission endpoint returns them.
 *
 * `{ message, problems: [{ field, message }] }` — an object rather than a
 * string, so a form can mark each input rather than parsing sentences. Worth
 * handling here too: without it the whole body is unreadable and the caller
 * falls back to a generic string, which is what made "Could not submit for
 * review" appear in place of three specific, fixable reasons.
 */
function fromProblemObject(detail: Record<string, unknown>): string | null {
    const problems = detail.problems;
    const lines: string[] = [];

    if (typeof detail.message === "string" && detail.message) {
        lines.push(detail.message);
    }
    if (Array.isArray(problems)) {
        for (const problem of problems) {
            if (!problem || typeof problem !== "object") continue;
            const p = problem as { field?: unknown; message?: unknown };
            if (typeof p.message !== "string" || !p.message) continue;
            lines.push(
                typeof p.field === "string" && p.field
                    ? `${p.field}: ${p.message}`
                    : p.message,
            );
        }
    }
    return lines.length > 0 ? lines.join("\n") : null;
}

export function detailFromError(err: unknown, fallback = "Request failed"): string {
    if (typeof err === "string") return err;
    const e = err as { detail?: unknown };
    if (typeof e?.detail === "string") return e.detail;
    if (e?.detail && typeof e.detail === "object" && !Array.isArray(e.detail)) {
        const message = fromProblemObject(e.detail as Record<string, unknown>);
        if (message) return message;
    }
    if (Array.isArray(e?.detail) && e.detail.length > 0) {
        const messages = e.detail
            .map((item) => {
                if (typeof item === "string") return item;
                if (!item || typeof item !== "object") return null;
                const detail = item as {
                    message?: unknown;
                    msg?: unknown;
                    model?: unknown;
                    loc?: unknown;
                };
                const message = typeof detail.message === "string"
                    ? detail.message
                    : typeof detail.msg === "string"
                        ? detail.msg
                        : null;
                if (!message) return null;
                if (typeof detail.model === "string" && detail.model) {
                    return `${detail.model}: ${message}`;
                }
                // Without this a 422 over two blank fields renders as
                // "Field required" twice, naming neither — which is what the
                // verification form showed, and there is no way to act on it.
                const field = fieldFromLoc(detail.loc);
                return field ? `${field}: ${message}` : message;
            })
            .filter((message): message is string => Boolean(message));
        if (messages.length > 0) return messages.join("\n");
    }
    return fallback;
}
