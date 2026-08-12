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

export function detailFromError(err: unknown, fallback = "Request failed"): string {
    if (typeof err === "string") return err;
    const e = err as { detail?: unknown };
    if (typeof e?.detail === "string") return e.detail;
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
