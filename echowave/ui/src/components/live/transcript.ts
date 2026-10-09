/**
 * The listen panel's transcript, built from the call's events.
 *
 * The server sends a line again every time it grows, under the same `line`
 * number, until it is final; a whisper is its own entry. Every event has a
 * `seq`, one counter for the whole call, so the backlog a late listener is
 * handed and the events that arrive live merge into one order, and an event
 * seen twice (in the backlog and live) is applied once.
 * See api/services/live_supervision/lines.py.
 *
 * With live_takeover, two more: `takeover` (somebody joined, switched, let
 * the agent answer, handed back, or dropped off and the agent took the call
 * back) and `supervisor` (one stretch of a supervisor speaking to the
 * caller, sent when it starts and again, final, when it ends). Both are
 * entries in the transcript, and `takeover` also keeps `state.takeover`:
 * who has the call now. See api/services/live_takeover/controller.py.
 */

export type LiveEvent = {
    type: string;
    seq?: number | null;
    at?: string;
    speaker?: 'caller' | 'agent';
    line?: number;
    text?: string;
    final?: boolean;
    cut_off?: boolean;
    id?: string;
    by?: string;
    urgent?: boolean;
    step?: string | null;
    detail?: string;
    /** `takeover`: what happened, and who has the call after it. */
    action?: string;
    mode?: string;
    by_user_id?: number | null;
    /** `takeover` on a recovery: who dropped off. */
    supervisor?: string;
    /** `supervisor`: how long they spoke, once final. */
    seconds?: number;
};

export type TakeoverMode = 'ai' | 'barge' | 'takeover';

export type TakeoverLive = {
    mode: TakeoverMode;
    by: string | null;
    byUserId: number | null;
    agentAnswering: boolean;
};

export const AGENT_HAS_IT: TakeoverLive = { mode: 'ai', by: null, byUserId: null, agentAnswering: false };

export type TranscriptEntry =
    | {
          kind: 'line';
          key: string;
          seq: number;
          speaker: 'caller' | 'agent';
          text: string;
          final: boolean;
          cutOff: boolean;
      }
    | { kind: 'whisper'; key: string; seq: number; by: string; text: string; urgent: boolean }
    | { kind: 'takeover'; key: string; seq: number; text: string }
    | { kind: 'supervisor'; key: string; seq: number; by: string; seconds: number; final: boolean };

export type TranscriptState = {
    entries: TranscriptEntry[];
    seen: Set<number>;
    step: string | null;
    ended: boolean;
    interrupted: number;
    /** Who has the call: the agent, or a supervisor and how. */
    takeover: TakeoverLive;
};

export const EMPTY: TranscriptState = {
    entries: [],
    seen: new Set(),
    step: null,
    ended: false,
    interrupted: 0,
    takeover: AGENT_HAS_IT,
};

function asMode(mode: string | undefined): TakeoverMode {
    return mode === 'barge' || mode === 'takeover' ? mode : 'ai';
}

/** One line for a change of who has the call, in the panel and on the
 *  call's record alike. */
export function takeoverText(event: Pick<LiveEvent, 'action' | 'mode' | 'by' | 'supervisor' | 'detail'>): string {
    const by = event.by || 'A supervisor';
    switch (event.action) {
        case 'joined':
            return event.mode === 'takeover'
                ? `${by} took over the call. The agent is silent until it is handed back.`
                : `${by} joined the call. The agent is paused.`;
        case 'switched':
            return event.mode === 'takeover'
                ? `${by} took over the call. The agent is silent until it is handed back.`
                : `${by} switched to barge. The agent is paused.`;
        case 'agent_answering':
            return `${by} let the agent answer.`;
        case 'agent_paused':
            return `The agent stopped as ${by} spoke.`;
        case 'handed_back':
            return `${by} handed the call back to the agent.`;
        case 'recovered':
            return `${event.supervisor || by} dropped off the call. The agent took it back.`;
        case 'failed':
            return `${by} could not join the call. ${event.detail ?? ''}`.trim();
        default:
            return `${by} changed who has the call.`;
    }
}

function nextTakeover(was: TakeoverLive, event: LiveEvent): TakeoverLive {
    switch (event.action) {
        case 'joined':
        case 'switched':
            return { mode: asMode(event.mode), by: event.by ?? null, byUserId: event.by_user_id ?? null, agentAnswering: false };
        case 'agent_answering':
            return { ...was, agentAnswering: true };
        case 'agent_paused':
            return { ...was, agentAnswering: false };
        case 'handed_back':
        case 'recovered':
        case 'failed':
            return AGENT_HAS_IT;
        default:
            return was;
    }
}

/** Where an entry first appeared: a line keeps the place of its first words. */
function place(entries: TranscriptEntry[], entry: TranscriptEntry): TranscriptEntry[] {
    const at = entries.findIndex((e) => e.key === entry.key);
    if (at >= 0) {
        const next = entries.slice();
        next[at] = { ...entry, seq: entries[at].seq };
        return next;
    }
    const next = [...entries, entry];
    next.sort((a, b) => a.seq - b.seq);
    return next;
}

export function apply(state: TranscriptState, event: LiveEvent): TranscriptState {
    const seq = typeof event.seq === 'number' ? event.seq : null;
    if (seq !== null && state.seen.has(seq)) return state;
    const seen = seq !== null ? new Set(state.seen).add(seq) : state.seen;
    const order = seq ?? Number.MAX_SAFE_INTEGER;
    switch (event.type) {
        case 'line': {
            if (!event.speaker || typeof event.line !== 'number') return { ...state, seen };
            const key = `line-${event.line}`;
            const existing = state.entries.find((e) => e.key === key);
            // A late, out-of-order interim must not overwrite a final line.
            if (existing && existing.kind === 'line' && existing.final && !event.final) return { ...state, seen };
            return {
                ...state,
                seen,
                entries: place(state.entries, {
                    kind: 'line',
                    key,
                    seq: order,
                    speaker: event.speaker,
                    text: event.text ?? '',
                    final: Boolean(event.final),
                    cutOff: Boolean(event.cut_off),
                }),
            };
        }
        case 'whisper':
            return {
                ...state,
                seen,
                entries: place(state.entries, {
                    kind: 'whisper',
                    key: `whisper-${event.id ?? order}`,
                    seq: order,
                    by: event.by || 'A supervisor',
                    text: event.text ?? '',
                    urgent: Boolean(event.urgent),
                }),
            };
        case 'takeover':
            return {
                ...state,
                seen,
                takeover: nextTakeover(state.takeover, event),
                entries: place(state.entries, {
                    kind: 'takeover',
                    key: `takeover-${order}`,
                    seq: order,
                    text: takeoverText(event),
                }),
            };
        case 'supervisor': {
            const key = `supervisor-${event.id ?? order}`;
            const existing = state.entries.find((e) => e.key === key);
            if (existing && existing.kind === 'supervisor' && existing.final && !event.final) return { ...state, seen };
            return {
                ...state,
                seen,
                entries: place(state.entries, {
                    kind: 'supervisor',
                    key,
                    seq: order,
                    by: event.by || 'A supervisor',
                    seconds: event.seconds ?? 0,
                    final: Boolean(event.final),
                }),
            };
        }
        case 'step':
            return { ...state, seen, step: event.step ?? null };
        case 'interrupted':
            return { ...state, seen, interrupted: state.interrupted + 1 };
        case 'ended':
            return { ...state, seen, ended: true };
        default:
            return { ...state, seen };
    }
}

export function applyAll(state: TranscriptState, events: LiveEvent[]): TranscriptState {
    return events.reduce(apply, state);
}

/** "3:07", "1:02:45": how long a call has been going. */
export function duration(seconds: number): string {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${rest}` : `${m}:${rest}`;
}
