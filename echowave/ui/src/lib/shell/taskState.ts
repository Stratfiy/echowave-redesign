/**
 * The task states of the handoff's shared contract ("Task state"), and the
 * state of one Chat turn derived from the thread's own rows.
 *
 * The full set is the contract every stream renders with TaskStatus:
 * queued, running, needs input, awaiting approval, scheduled, completed,
 * failed, cancelled and outcome unknown -- plus `partial` for an answer the
 * person stopped. Completion is only ever read from a row the server wrote;
 * a reply that has not landed is running, never done.
 */

export type TaskState =
    | "queued"
    | "running"
    | "needs_input"
    | "awaiting_approval"
    | "scheduled"
    | "completed"
    | "failed"
    | "cancelled"
    | "outcome_unknown"
    | "partial";

export const TASK_STATES: readonly TaskState[] = [
    "queued",
    "running",
    "needs_input",
    "awaiting_approval",
    "scheduled",
    "completed",
    "failed",
    "cancelled",
    "outcome_unknown",
    "partial",
];

/** What each state says, in the person's words. */
export const TASK_STATE_LABEL: Record<TaskState, string> = {
    queued: "Queued",
    running: "Working",
    needs_input: "Needs your input",
    awaiting_approval: "Waiting for your approval",
    scheduled: "Scheduled",
    completed: "Done",
    failed: "Failed",
    cancelled: "Cancelled",
    outcome_unknown: "Checking what happened",
    partial: "Stopped · partial answer",
};

/** States that are still moving: announced politely when they change, and
 *  never shown with a success mark. */
export const ACTIVE_STATES: ReadonlySet<TaskState> = new Set(["queued", "running", "scheduled"]);

type Row = {
    id: number;
    at: string;
    kind: string;
    actor: string;
    summary?: string;
    workflow_id?: number | null;
    payload?: Record<string, unknown> | null;
};

export type TurnStatus = {
    state: TaskState;
    /** A real stage, from the last activity row ("Read the team ..."). */
    stage?: string;
    /** The person's line this status belongs under. */
    requestId: number;
};

function payload(row: Row): Record<string, unknown> {
    return (row.payload ?? {}) as Record<string, unknown>;
}

/** A proposed action still waiting on the person. */
function awaitingApproval(row: Row): boolean {
    if (row.kind !== "action_proposed" && row.kind !== "edit_proposed") return false;
    const state = (payload(row).state as string | undefined) ?? "proposed";
    return state === "proposed";
}

function needsInput(row: Row): boolean {
    if (row.kind === "needs_secret") return !payload(row).provided;
    if (row.kind === "needs_decision") return !payload(row).decided;
    if (row.kind === "connector_offered") return true;
    return false;
}

/**
 * The state of the latest turn in an assistant thread, or null when there
 * is no turn to describe (no line from the person yet).
 *
 * `rows` are oldest first. `waiting` is true while the screen is still
 * waiting for the reply (the thinking row is up).
 */
export function latestTurnStatus(rows: readonly Row[], waiting: boolean): TurnStatus | null {
    let requestIndex = -1;
    for (let i = rows.length - 1; i >= 0; i -= 1) {
        if (rows[i].actor === "human") {
            requestIndex = i;
            break;
        }
    }
    if (requestIndex < 0) return null;
    const request = rows[requestIndex];
    const after = rows.slice(requestIndex + 1).filter((row) => row.workflow_id == null);
    const activities = after.filter((row) => row.kind === "activity");
    const stage = activities.length ? activities[activities.length - 1].summary : undefined;

    if (after.some(needsInput)) return { state: "needs_input", requestId: request.id, stage };
    if (after.some(awaitingApproval)) return { state: "awaiting_approval", requestId: request.id, stage };

    const reply = [...after].reverse().find((row) => row.kind === "message" && row.actor !== "human");
    if (waiting && !reply) return { state: "running", requestId: request.id, stage };
    if (!reply) {
        // Nothing came back and nobody is waiting any more: the screen gave
        // up on it. Not a success, and not a failure we can name.
        return waiting ? { state: "running", requestId: request.id, stage } : null;
    }
    const p = payload(reply);
    if (p.stopped) return { state: "partial", requestId: request.id, stage };
    if (p.failed) return { state: "failed", requestId: request.id, stage };
    return { state: "completed", requestId: request.id, stage };
}

export type SourceRead = {
    kind: string;
    label: string;
    status: "read" | "unavailable" | "failed";
    detail?: string;
    documents?: string[];
};

/** The sources a reply was read from: every `sources` on the activity rows
 *  of the same turn, in the order they were read -- the readings row
 *  (services/workflow/decibyl.sources_read) and any file search after it
 *  (search_files, an agent's knowledge lookup). A source named twice is one
 *  entry, with the files of both, so a file read by the second search is
 *  not hidden behind the first. */
export function sourcesForReply(rows: readonly Row[], replyId: number): SourceRead[] {
    const index = rows.findIndex((row) => row.id === replyId);
    if (index < 0) return [];
    const found: SourceRead[][] = [];
    for (let i = index - 1; i >= 0; i -= 1) {
        const row = rows[i];
        if (row.actor === "human") break;
        const sources = payload(row).sources;
        if (row.kind === "activity" && Array.isArray(sources)) found.unshift(sources as SourceRead[]);
    }
    const merged: SourceRead[] = [];
    for (const source of found.flat()) {
        const same = merged.find((s) => s.kind === source.kind && s.label === source.label);
        if (!same) {
            merged.push({ ...source, documents: source.documents ? [...source.documents] : undefined });
            continue;
        }
        if (source.status === "read") same.status = "read";
        for (const doc of source.documents ?? []) {
            same.documents = same.documents ?? [];
            if (!same.documents.includes(doc)) same.documents.push(doc);
        }
    }
    return merged;
}
