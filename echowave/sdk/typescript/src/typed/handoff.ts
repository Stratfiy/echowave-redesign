// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Decibyl backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Pass the call to one of your other agents, mid-conversation.
 *
 * LLM hint: Hands the live call over to another agent in the same account. That agent's conversation is spliced into this one when the call starts, so from the caller's side it is one continuous call — no ringing, no hold, no second greeting.
 *
 * Use it when a business has genuinely different jobs and the second one is worth maintaining on its own: a receptionist that hands to a billing agent, an ordering agent that hands to a returns agent. The point is reuse — build the specialist once and put it behind several front doors.
 *
 * **It is a transfer, not a detour.** The call continues with the other agent and does not come back, so this node has nothing after it. If the conversation needs to return, it is a branch in one agent rather than a handoff to another.
 *
 * The other agent's voice and model are not adopted — the call keeps the one it started with. What is reused is the conversation: its prompts, its steps, its tools and its own global instructions.
 */
export interface Handoff {
    type: "handoff";
    /**
     * Short identifier shown in the canvas and call logs.
     */
    name?: string;
    /**
     * Which of your agents takes the call from here. It must belong to this account, and the two must not hand back to each other.
     */
    agent_uuid?: unknown;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function handoff(input: Omit<Handoff, "type">): Handoff {
    return { type: "handoff", ...input };
}
