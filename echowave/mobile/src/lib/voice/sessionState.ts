/**
 * Live voice with Decibyl: the screen's state machine (screen 05, handoff 21).
 * The same machine as the web (ui/src/lib/voice/sessionState.ts), so the
 * app and the site move through the same states on the same events; labels
 * come from i18n (`voice.state.<phase>`).
 *
 *   idle -> requesting_mic -> connecting -> listening <-> processing <-> speaking
 *
 * plus the recoverable and final states the design names: microphone denied,
 * no microphone, needs setup, over the daily limit, connecting timeout,
 * reconnecting, ended and failed. Kept pure -- events in, state out -- so
 * the sheet, the hook and the tests read one contract, and a late event
 * (a transcript after End) cannot move an ended session.
 */

export type VoicePhase =
    | 'idle'
    | 'requesting_mic'
    | 'connecting'
    | 'listening'
    | 'processing'
    | 'speaking'
    | 'reconnecting'
    | 'mic_denied'
    | 'device_missing'
    | 'needs_setup'
    | 'limit_reached'
    | 'connect_timeout'
    | 'already_live'
    | 'ended'
    | 'failed';

export type Caption = { id: string; who: 'you' | 'decibyl'; text: string; final: boolean };

export type VoiceState = {
    phase: VoicePhase;
    sessionId: number | null;
    stateVersion: number | null;
    muted: boolean;
    /** Said under the state label: why it stopped, how much audio was lost. */
    notice: string | null;
    captions: Caption[];
    /** A turn asked for a card: the exact action waits in Chat for a tap. */
    approvalWaiting: boolean;
    minimized: boolean;
};

export type VoiceEvent =
    | { type: 'open' }
    | { type: 'mic_requested' }
    | { type: 'mic_denied' }
    | { type: 'mic_missing' }
    | { type: 'refused'; code: string; message: string }
    | { type: 'session_started'; sessionId: number; stateVersion: number }
    | { type: 'connect_timeout' }
    | { type: 'live'; stateVersion?: number; gapSentence?: string | null }
    | { type: 'processing' }
    | { type: 'bot_started' }
    | { type: 'bot_stopped' }
    | { type: 'user_caption'; text: string; final: boolean }
    | { type: 'bot_caption'; text: string }
    | { type: 'turn_closed'; toolTurn: boolean }
    | { type: 'muted'; muted: boolean; stateVersion?: number }
    | { type: 'connection_lost' }
    | { type: 'error'; message: string }
    | { type: 'minimize'; minimized: boolean }
    | { type: 'ended'; notice?: string | null }
    | { type: 'reset' };

export const INITIAL: VoiceState = {
    phase: 'idle',
    sessionId: null,
    stateVersion: null,
    muted: false,
    notice: null,
    captions: [],
    approvalWaiting: false,
    minimized: false,
};

/** Phases where the microphone may be capturing and End must release it. */
export const ACTIVE: ReadonlySet<VoicePhase> = new Set([
    'requesting_mic',
    'connecting',
    'listening',
    'processing',
    'speaking',
    'reconnecting',
]);

/** Phases the person can only leave by starting again or closing. */
export const FINAL: ReadonlySet<VoicePhase> = new Set([
    'mic_denied',
    'device_missing',
    'needs_setup',
    'limit_reached',
    'connect_timeout',
    'already_live',
    'ended',
    'failed',
]);

const REFUSALS: Record<string, VoicePhase> = {
    needs_setup: 'needs_setup',
    disabled_by_policy: 'needs_setup',
    unavailable: 'failed',
    voice_limit_reached: 'limit_reached',
    already_live: 'already_live',
    session_ended: 'ended',
    config_changed: 'ended',
};

const MAX_CAPTIONS = 40;

function addCaption(captions: Caption[], next: Caption): Caption[] {
    const last = captions[captions.length - 1];
    let out: Caption[];
    if (last && last.who === next.who && !last.final) {
        // The words of one utterance replace each other until final, so the
        // caption grows in place rather than stacking every interim.
        const text = next.who === 'decibyl' ? `${last.text}${next.text}` : next.text;
        out = [...captions.slice(0, -1), { ...last, text, final: next.final }];
    } else {
        out = [...captions, next];
    }
    return out.slice(-MAX_CAPTIONS);
}

let captionSeq = 0;

export function reduce(state: VoiceState, event: VoiceEvent): VoiceState {
    if (event.type === 'reset') return INITIAL;
    if (event.type === 'minimize') return { ...state, minimized: event.minimized };
    if (event.type === 'open') {
        return { ...INITIAL, phase: 'idle' };
    }
    // Nothing moves an ended or final session except opening a new one.
    if (FINAL.has(state.phase)) return state;
    switch (event.type) {
        case 'mic_requested':
            return { ...state, phase: 'requesting_mic', notice: null };
        case 'mic_denied':
            return {
                ...state,
                phase: 'mic_denied',
                notice: 'Decibyl cannot hear you until the microphone is allowed in your phone settings.',
            };
        case 'mic_missing':
            return { ...state, phase: 'device_missing', notice: 'Connect a microphone, or continue in text.' };
        case 'refused':
            return { ...state, phase: REFUSALS[event.code] ?? 'failed', notice: event.message };
        case 'session_started':
            return { ...state, phase: 'connecting', sessionId: event.sessionId, stateVersion: event.stateVersion };
        case 'connect_timeout':
            return { ...state, phase: 'connect_timeout', notice: 'The connection did not open. Try again, or continue in text.' };
        case 'live':
            return {
                ...state,
                phase: 'listening',
                stateVersion: event.stateVersion ?? state.stateVersion,
                notice: event.gapSentence ?? (state.phase === 'reconnecting' ? state.notice : null),
            };
        case 'processing':
            return state.phase === 'reconnecting' ? state : { ...state, phase: 'processing' };
        case 'bot_started':
            return state.phase === 'reconnecting' ? state : { ...state, phase: 'speaking' };
        case 'bot_stopped':
            return state.phase === 'speaking' ? { ...state, phase: 'listening' } : state;
        case 'user_caption':
            return {
                ...state,
                captions: addCaption(state.captions, {
                    id: `c${++captionSeq}`,
                    who: 'you',
                    text: event.text,
                    final: event.final,
                }),
            };
        case 'bot_caption':
            return {
                ...state,
                captions: addCaption(state.captions, {
                    id: `c${++captionSeq}`,
                    who: 'decibyl',
                    text: event.text,
                    final: false,
                }),
            };
        case 'turn_closed': {
            const captions = state.captions.map((c, i) =>
                i === state.captions.length - 1 && c.who === 'decibyl' ? { ...c, final: true } : c,
            );
            return { ...state, captions, approvalWaiting: state.approvalWaiting || event.toolTurn };
        }
        case 'muted':
            return { ...state, muted: event.muted, stateVersion: event.stateVersion ?? state.stateVersion };
        case 'connection_lost':
            return ACTIVE.has(state.phase)
                ? { ...state, phase: 'reconnecting', notice: 'Connection lost. Reconnecting…' }
                : state;
        case 'error':
            return { ...state, phase: 'failed', notice: event.message };
        case 'ended':
            return { ...state, phase: 'ended', notice: event.notice ?? null };
        default:
            return state;
    }
}
