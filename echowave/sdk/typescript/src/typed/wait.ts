// GENERATED — do not edit by hand.
//
// Regenerate with `npm run codegen` against the target Decibyl backend.
// Source of truth: the backend's model-backed node-spec catalog served
// from `/api/v1/node-types`.


/**
 * Pause the conversation for a moment before continuing.
 *
 * LLM hint: Holds the call for a fixed number of seconds, then continues down its single outgoing edge. No LLM turn is taken and the caller is not asked anything.
 *
 * Use it when the flow needs a beat that is not a question: after a tool call whose result the caller is waiting on, before repeating an important number, or to let a transfer announcement land. Do **not** use it to wait for the caller to speak — an agent node already waits for a turn, and a wait node on top of that is silence they will fill by hanging up.
 *
 * Say something during anything longer than about two seconds. Silence on a phone line reads as a dropped call, and `filler_text` is what stops the caller saying 'hello? hello?' into a working connection.
 */
export interface Wait {
    type: "wait";
    /**
     * Short identifier shown in the canvas and call logs.
     */
    name?: string;
    /**
     * How long to hold before continuing. Capped at 30 — a longer pause is a caller who has already hung up.
     */
    duration_seconds?: number;
    /**
     * What the caller hears during the pause.
     */
    filler_type?: "none" | "text" | "audio";
    /**
     * Spoken once at the start of the pause. Supports {{template_variables}}.
     */
    filler_text?: string;
    /**
     * Pre-recorded audio played at the start of the pause.
     */
    filler_recording_id?: string;
}

/** Factory — sets `type` for you so you don't repeat the discriminator. */
export function wait(input: Omit<Wait, "type">): Wait {
    return { type: "wait", ...input };
}
