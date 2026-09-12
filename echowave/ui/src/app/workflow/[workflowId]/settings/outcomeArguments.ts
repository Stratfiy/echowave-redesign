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

/** Every variable this agent actually collects, across all its nodes.
 *
 * The reason the after-call editor should not be asking anybody to type Jinja:
 * the agent already declares these, three screens away, in
 * `extraction_variables` on each node. Somebody hand-writing
 * `{{ gathered_context.extracted_variables.origin_city }}` is retyping a name
 * the agent itself defined.
 *
 * Defensive throughout: `workflow_definition` is a free-form JSON column that
 * has been through the API, a database and a canvas editor, so every level is
 * checked rather than trusted.
 */
export function extractionVariables(definition: unknown): string[] {
    const nodes = (definition as { nodes?: unknown } | null)?.nodes;
    if (!Array.isArray(nodes)) return [];

    const names: string[] = [];
    for (const node of nodes) {
        const variables = (node as { data?: { extraction_variables?: unknown } })?.data
            ?.extraction_variables;
        if (!Array.isArray(variables)) continue;
        for (const variable of variables) {
            const name = (variable as { name?: unknown })?.name;
            if (typeof name === "string" && name.trim()) names.push(name.trim());
        }
    }
    // One agent can extract the same variable on several nodes; the editor
    // wants the vocabulary, not the occurrences.
    return [...new Set(names)];
}

/** The expression that reads one collected variable. */
export function variableToken(name: string): string {
    return `{{ gathered_context.extracted_variables.${name} }}`;
}

/** Words that mean the same thing to a business and different things to two
 *  people naming fields.
 *
 *  Kept short and one-directional-by-pair: a longer list starts guessing, and
 *  a wrong guess here is a wrong value proposed into somebody's CRM field. */
const SYNONYMS: readonly (readonly string[])[] = [
    ["phone", "mobile", "contact number", "phone number", "whatsapp number"],
    ["email", "mail", "email address"],
    ["company", "business", "organisation", "organization", "firm"],
    ["name", "customer name", "caller name", "contact name", "full name"],
    ["city", "town"],
    ["address", "location"],
    ["pincode", "pin code", "postal code", "zip"],
];

/** Underscores, hyphens and case are naming style, not meaning. */
function normalise(value: string): string {
    return value.toLowerCase().replace(/[_\-\s]+/g, " ").trim();
}

function sameFamily(a: string, b: string): boolean {
    return SYNONYMS.some((family) => family.includes(a) && family.includes(b));
}

/** Which collected variable a tool parameter should read, or null.
 *
 * **Null rather than a guess.** An unmatched field is left blank for somebody
 * to fill, because a plausible-but-wrong value proposed into a CRM field is
 * worse than an empty one: the blank gets noticed and the wrong value gets
 * saved. So the match has to be an exact name, a synonym, or one name wholly
 * containing the other -- never a partial-word resemblance.
 */
export function suggestVariableFor(
    parameterName: string,
    variables: readonly string[],
): string | null {
    const parameter = normalise(parameterName);
    if (!parameter) return null;

    const candidates = variables.map((name) => ({ name, key: normalise(name) }));

    const exact = candidates.find((candidate) => candidate.key === parameter);
    if (exact) return exact.name;

    const synonym = candidates.find((candidate) =>
        sameFamily(parameter, candidate.key),
    );
    if (synonym) return synonym.name;

    // Whole-phrase containment only: "city" matches "origin city", and
    // "phone" does not match "telephone_consent". Guarded on length so a
    // two-letter parameter cannot match everything.
    if (parameter.length >= 4) {
        const contained = candidates.find(
            (candidate) =>
                candidate.key.split(" ").includes(parameter) ||
                parameter.split(" ").includes(candidate.key),
        );
        if (contained) return contained.name;
    }
    return null;
}

/** The prefill for a whole tool: every parameter we can match, and nothing we
 *  cannot. Existing values always win -- this proposes, it never overwrites. */
export function suggestArguments(
    parameters: readonly ToolParameter[],
    variables: readonly string[],
    existing: Record<string, string> = {},
): Record<string, string> {
    const out: Record<string, string> = { ...existing };
    for (const parameter of parameters) {
        if (out[parameter.name]) continue;
        const match = suggestVariableFor(parameter.name, variables);
        if (match) out[parameter.name] = variableToken(match);
    }
    return out;
}
