// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Decibyl backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.

/**
 * Evaluated top to bottom. The first rule that matches decides the branch; drag to reorder.
 */
export interface BranchRulesRow {
    /**
     * Label of the outgoing edge to take when this rule matches. Must match one of this node's edge labels exactly.
     */
    label: string;
    /**
     * Name of the variable to test — from extraction, the trigger payload, a campaign column, or a pre-call fetch.
     */
    variable: string;
    /**
     * How the variable is compared to the value.
     */
    operator: string;
    /**
     * What to compare against. Leave blank for is_empty, is_not_empty, is_true and is_false. For 'is one of', a comma-separated list.
     */
    value?: string;
}

/**
 * Route on a variable — same input, same path, every time.
 *
 * LLM hint: Deterministic routing. Every other transition in a workflow is decided by the LLM from the edge's natural-language condition; a branch node is decided by evaluating a rule, with no model involved.
 *
 * Use it whenever the decision is a fact rather than a judgement: a threshold on an amount, a language the caller already chose, an attempt count, a flag from a pre-call fetch. Keep LLM-decided edges for judgements about what the caller meant.
 *
 * Rules are evaluated top to bottom and the first match wins, so order bands from most specific down. Every rule's `label` must match an outgoing edge label, and `default_label` must match one too — that edge is taken when nothing matches, so a call can never be stranded here.
 *
 * The node never speaks. It routes on entry, and the caller hears nothing between the previous node and the next one.
 */
export interface Branch {
    type: "branch";
    /**
     * Short identifier shown in the canvas and call logs.
     */
    name?: string;
    /**
     * Evaluated top to bottom. The first rule that matches decides the branch; drag to reorder.
     */
    rules?: Array<BranchRulesRow>;
    /**
     * Edge taken when no rule matches. Required — every call that reaches this node must have somewhere to go.
     */
    default_label?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function branch(input: Omit<Branch, "type">): Branch {
    return { type: "branch", ...input };
}
