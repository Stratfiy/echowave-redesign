/**
 * The listen panel's transcript, built from the call's events.
 *
 * The server sends a line again every time it grows, under the same `line`
 * number, until it is final; a whisper is its own entry. Every event has a
 * `seq`, one counter for the whole call, so the backlog a late listener is
 * handed and the events that arrive live merge into one order, and an event
 * seen twice (in the backlog and live) is applied once.
 * See api/services/live_supervision/lines.py.
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
};

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
    | { kind: 'whisper'; key: string; seq: number; by: string; text: string; urgent: boolean };

export type TranscriptState = {
    entries: TranscriptEntry[];
    seen: Set<number>;
    step: string | null;
    ended: boolean;
    interrupted: number;
};

export const EMPTY: TranscriptState = { entries: [], seen: new Set(), step: null, ended: false, interrupted: 0 };

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
