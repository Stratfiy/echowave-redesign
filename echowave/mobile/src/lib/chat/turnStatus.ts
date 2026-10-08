/**
 * The state of the latest turn in a Decibyl thread, derived from the rows
 * the server wrote -- the same rule as the web (ui/src/lib/shell/taskState.ts).
 * Completion is only ever read from a row; a reply that has not landed is
 * running, never done.
 */
import { payloadOf, type Row } from './events';

export type TaskState =
    | 'queued'
    | 'running'
    | 'needs_input'
    | 'awaiting_approval'
    | 'scheduled'
    | 'completed'
    | 'failed'
    | 'cancelled'
    | 'outcome_unknown'
    | 'partial';

export const ACTIVE_STATES: ReadonlySet<TaskState> = new Set(['queued', 'running', 'scheduled']);

export type TurnStatus = { state: TaskState; stage?: string; requestId: number };

function awaitingApproval(row: Row): boolean {
    if (row.kind !== 'action_proposed' && row.kind !== 'edit_proposed') return false;
    const state = (payloadOf(row).state as string | undefined) ?? 'proposed';
    return state === 'proposed';
}

function needsInput(row: Row): boolean {
    if (row.kind === 'needs_secret') return !payloadOf(row).provided;
    if (row.kind === 'needs_decision') return !payloadOf(row).decided;
    if (row.kind === 'connector_offered') return true;
    return false;
}

/** `rows` oldest first; `waiting` while the screen waits for the reply. */
export function latestTurnStatus(rows: readonly Row[], waiting: boolean): TurnStatus | null {
    let requestIndex = -1;
    for (let i = rows.length - 1; i >= 0; i -= 1) {
        if (rows[i].actor === 'human') {
            requestIndex = i;
            break;
        }
    }
    if (requestIndex < 0) return null;
    const request = rows[requestIndex];
    const after = rows.slice(requestIndex + 1).filter((row) => row.workflow_id == null);
    const activities = after.filter((row) => row.kind === 'activity');
    const stage = activities.length ? activities[activities.length - 1].summary : undefined;

    if (after.some(needsInput)) return { state: 'needs_input', requestId: request.id, stage };
    if (after.some(awaitingApproval)) return { state: 'awaiting_approval', requestId: request.id, stage };
    const reply = [...after].reverse().find((row) => row.kind === 'message' && row.actor !== 'human');
    if (!reply) return waiting ? { state: 'running', requestId: request.id, stage } : null;
    const p = payloadOf(reply);
    if (p.stopped) return { state: 'partial', requestId: request.id, stage };
    if (p.failed) return { state: 'failed', requestId: request.id, stage };
    return { state: 'completed', requestId: request.id, stage };
}
