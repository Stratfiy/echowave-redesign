/**
 * What an after-call step asks the operator to fill in.
 *
 * A tool already knows its own parameters — whoever built an HTTP tool named
 * them, and a Composio connector's slug determines them. Rendering a named
 * field with its description is the difference between "fill in this key/value
 * grid" and "which email address should this go to", and the data for the
 * second was already sitting in the definition.
 *
 * Its own module rather than the page's, because a Next.js page may export
 * nothing but the route's own contract, and a helper worth testing is worth
 * importing from somewhere a test can reach.
 */

export type ToolParameter = {
    name: string;
    description?: string;
    required?: boolean;
};

/** The fields a tool declares, or none.
 *
 * Defensive by necessity: `definition` is a free-form JSON column that has
 * been through the API, a database, and whatever an operator typed into the
 * tool builder. Google Calendar declares nothing — its shape is fixed in the
 * backend — and falls through to the free-form editor rather than carrying a
 * copy of that shape here, which would drift the first time it changed.
 */
export function toolParameters(definition: unknown): ToolParameter[] {
    const config = (definition as { config?: { parameters?: unknown } } | null)?.config;
    const raw = config?.parameters;
    if (!Array.isArray(raw)) return [];
    return raw
        .filter((entry): entry is ToolParameter =>
            Boolean(entry && typeof entry === "object" && "name" in entry),
        )
        .filter((entry) => typeof entry.name === "string" && entry.name.length > 0);
}

/** Values that exist on every call, whatever the agent collects.
 *
 * Deliberately short. Anything an agent extracts is named by whoever built it,
 * so a longer list would be a guess presented as a fact; the editor's hint
 * says how to reach those instead.
 */
export const ALWAYS_AVAILABLE = [
    { token: "{{ initial_context.phone_number }}", label: "Caller's number" },
    { token: "{{ call_time }}", label: "When the call happened" },
    { token: "{{ gathered_context.call_disposition }}", label: "How it ended" },
] as const;
