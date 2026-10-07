/**
 * Reading a Decibyl thread's rows (GET /api/v1/timeline).
 *
 * Rows are events, not chat messages: there is no `role` or `text` field.
 * Who said it is `actor` (human | agent | caller | system); the words are
 * `payload.body`, falling back to `summary` (truncated at 500 characters on
 * the server). These helpers are the app's copy of what
 * ui/src/components/channel/ChannelStream.tsx reads, so both read a row the
 * same way.
 */
import type { TimelineEvent } from '@/client/types.gen';

export type Row = Pick<TimelineEvent, 'id' | 'at' | 'kind' | 'actor' | 'summary' | 'payload'> & {
    workflow_id?: number | null;
    folder_id?: number | null;
};

export function payloadOf(row: Row): Record<string, unknown> {
    return (row.payload ?? {}) as Record<string, unknown>;
}

export function textOf(row: Row): string {
    const body = payloadOf(row).body;
    return typeof body === 'string' && body ? body : row.summary || '';
}

export function isMine(row: Row): boolean {
    return row.actor === 'human';
}

/** A reply from Decibyl itself (not a bot in a channel, not a refusal line). */
export function isDecibylReply(row: Row): boolean {
    const p = payloadOf(row);
    return (
        row.kind === 'message' &&
        row.actor === 'agent' &&
        row.workflow_id == null &&
        row.folder_id == null &&
        Boolean(p.from) &&
        !p.quota &&
        !p.action_event_id
    );
}

export type Attachment = { document_uuid: string; filename: string; size_bytes: number };

export function attachmentsOf(row: Row): Attachment[] {
    const value = payloadOf(row).attachments;
    return Array.isArray(value) ? (value as Attachment[]) : [];
}

/** Rows a person sees in the thread, oldest first. Activity rows are stages
 * of a turn (shown in the status line), not lines of the conversation. */
export function visibleRows(rows: readonly Row[]): Row[] {
    return rows.filter((row) => row.kind !== 'activity');
}

/** The server returns newest first; merge a page into what is held, oldest
 * first, with each id once (a refetch must never duplicate a line). */
export function mergeRows(held: readonly Row[], page: readonly Row[]): Row[] {
    const byId = new Map<number, Row>();
    for (const row of held) byId.set(row.id, row);
    for (const row of page) byId.set(row.id, row);
    return [...byId.values()].sort((a, b) => (a.at === b.at ? a.id - b.id : a.at < b.at ? -1 : 1));
}

/** Whether a reply landed after `since` (ISO): ends the "working" wait. */
export function replyLandedSince(rows: readonly Row[], since: string): boolean {
    return rows.some((row) => row.actor !== 'human' && row.kind !== 'activity' && row.at > since);
}
