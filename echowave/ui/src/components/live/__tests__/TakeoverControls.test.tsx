/**
 * Joining a live call: nothing while the feature is off; two ways in, each
 * behind a confirm step, the microphone asked for only then and before the
 * join; a banner that cannot be missed while the caller can hear you; and
 * every wall named with the way past it.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => vi.fn());
const join = vi.hoisted(() => vi.fn());
const letAnswer = vi.hoisted(() => vi.fn());
const handBack = vi.hoisted(() => vi.fn());
const setJoinSettings = vi.hoisted(() => vi.fn());
const openMic = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ live_takeover: true }) as Record<string, boolean>);

vi.mock('@/client/sdk.gen', () => ({
    takeoverStateApiV1LiveCallsRunIdTakeoverGet: state,
    joinCallApiV1LiveCallsRunIdTakeoverPost: join,
    letAgentAnswerApiV1LiveCallsRunIdLetAgentAnswerPost: letAnswer,
    handBackApiV1LiveCallsRunIdHandBackPost: handBack,
    setJoinSettingsApiV1LiveCallsJoinSettingsPut: setJoinSettings,
}));
vi.mock('@/lib/auth', () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: async () => 'tok' }),
}));
vi.mock('@/lib/features', () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock('@/client/client.gen', () => ({ client: { getConfig: () => ({ baseUrl: 'http://api.test' }) } }));
vi.mock('../talkAudio', async (original) => {
    const real = await original<typeof import('../talkAudio')>();
    return { ...real, Microphone: { open: openMic } };
});

import { JOIN_WARNING, SILENT_TAKEOVER_COPY, TakeoverControls } from '../TakeoverControls';
import { AGENT_HAS_IT, type TakeoverLive } from '../transcript';

class FakeSocket {
    static OPEN = 1;
    static all: FakeSocket[] = [];
    binaryType = '';
    readyState = 1;
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onclose: (() => void) | null = null;
    sent: unknown[] = [];
    closed = false;
    constructor(
        public url: string,
        public protocols: string[],
    ) {
        FakeSocket.all.push(this);
    }
    send(data: unknown) {
        this.sent.push(data);
    }
    close() {
        this.closed = true;
    }
    emit(event: Record<string, unknown>) {
        act(() => this.onmessage?.({ data: JSON.stringify(event) }));
    }
}

class FakeMic {
    onSamples: ((samples: Float32Array, rate: number) => void) | null = null;
    closed = false;
    close() {
        this.closed = true;
    }
}

const STATE = {
    run_id: 41,
    mode: 'ai',
    by: null,
    by_user_id: null,
    since: null,
    agent_answering: false,
    mine: false,
    can_join: true,
    blocked: null,
    allow_joining: true,
    can_change_setting: true,
    bridge: 'pipeline',
    needs_phone: false,
    recovery_seconds: 5,
};

function respond(over: Record<string, unknown> = {}) {
    state.mockResolvedValue({ data: { ...STATE, ...over } });
}

function show(live: TakeoverLive = AGENT_HAS_IT, onSpeakingChange?: (speaking: boolean) => void) {
    return render(<TakeoverControls runId={41} live={live} ended={false} onSpeakingChange={onSpeakingChange} />);
}

const ME_BARGING: TakeoverLive = { mode: 'barge', by: 'priya', byUserId: 1, agentAnswering: false };

beforeEach(() => {
    FakeSocket.all = [];
    for (const fn of [state, join, letAnswer, handBack, setJoinSettings, openMic]) fn.mockReset();
    flags.live_takeover = true;
    vi.stubGlobal('WebSocket', FakeSocket);
});
afterEach(() => vi.unstubAllGlobals());

describe('the switch', () => {
    it('shows nothing and asks nothing while the feature is off', () => {
        flags.live_takeover = false;
        const { container } = show();
        expect(container.innerHTML).toBe('');
        expect(state).not.toHaveBeenCalled();
    });

    it('offers an admin the switch, with what it means', async () => {
        respond({ can_join: false, blocked: 'joining_off', allow_joining: false });
        setJoinSettings.mockResolvedValue({ data: { allow_joining: true } });
        show();
        expect(await screen.findByText('Joining calls is off for this workspace.')).toBeTruthy();
        expect(screen.getByText(/Every join is recorded in the audit log/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Turn on joining calls' }));
        await waitFor(() => expect(setJoinSettings).toHaveBeenCalledWith({ body: { allow_joining: true } }));
    });

    it('tells a member who can switch it', async () => {
        respond({ can_join: false, blocked: 'joining_off', allow_joining: false, can_change_setting: false });
        show();
        expect(await screen.findByText('A workspace admin or owner can turn it on.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Turn on joining calls' })).toBeNull();
    });
});

describe('joining', () => {
    it('confirms first, asks for the microphone before joining, then opens the talking socket', async () => {
        respond();
        const mic = new FakeMic();
        openMic.mockResolvedValue(mic);
        join.mockResolvedValue({ data: { mode: 'barge', by: 'priya', by_user_id: 1 } });
        show();
        fireEvent.click(await screen.findByRole('button', { name: /Barge in/ }));
        // Nothing yet: a confirm step that says the caller will hear you.
        expect(openMic).not.toHaveBeenCalled();
        expect(screen.getByText(JOIN_WARNING)).toBeTruthy();
        respond({ mode: 'barge', by: 'priya', by_user_id: 1, mine: true });
        fireEvent.click(screen.getByRole('button', { name: 'Turn on my microphone and join' }));
        await waitFor(() => expect(join).toHaveBeenCalledWith({ path: { run_id: 41 }, body: { mode: 'barge' } }));
        expect(openMic.mock.invocationCallOrder[0]).toBeLessThan(join.mock.invocationCallOrder[0]);
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        const ws = FakeSocket.all[0];
        expect(ws.url).toBe('ws://api.test/api/v1/ws/live-calls/41/talk');
        expect(ws.protocols).toEqual(['decibyl.auth', 'bearer.tok']);
        ws.emit({ type: 'live' });
        expect(await screen.findByText('You’re live. The caller can hear you.')).toBeTruthy();
        expect(screen.getByText('Barge: the agent is paused.')).toBeTruthy();
        // The microphone goes up the socket, side "s" at 16 kHz; muted, it does not.
        act(() => mic.onSamples?.(new Float32Array(4800).fill(0.2), 48000));
        expect(ws.sent).toHaveLength(1);
        const packet = new DataView(ws.sent[0] as ArrayBuffer);
        expect(String.fromCharCode(packet.getUint8(0))).toBe('s');
        expect(packet.getUint32(1, true)).toBe(16000);
        expect((ws.sent[0] as ArrayBuffer).byteLength).toBe(6 + 1600 * 2);
        fireEvent.click(screen.getByRole('button', { name: 'Mute' }));
        act(() => mic.onSamples?.(new Float32Array(4800).fill(0.2), 48000));
        expect(ws.sent).toHaveLength(1);
        expect(screen.getByText('Muted. The caller can’t hear you.')).toBeTruthy();
    });

    it('does not join when the microphone is refused', async () => {
        respond();
        openMic.mockRejectedValue(new Error('denied'));
        show();
        fireEvent.click(await screen.findByRole('button', { name: /Take over/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Turn on my microphone and join' }));
        expect((await screen.findByRole('alert')).textContent).toBe('Allow the microphone to speak on the call.');
        expect(join).not.toHaveBeenCalled();
    });

    it('says why a join was refused and lets the microphone go', async () => {
        respond();
        const mic = new FakeMic();
        openMic.mockResolvedValue(mic);
        join.mockResolvedValue({ error: { detail: 'user9 is already on this call.' } });
        show();
        fireEvent.click(await screen.findByRole('button', { name: /Barge in/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Turn on my microphone and join' }));
        expect((await screen.findByRole('alert')).textContent).toBe('user9 is already on this call.');
        expect(mic.closed).toBe(true);
        expect(FakeSocket.all).toHaveLength(0);
    });

    it('asks for a phone number when joining is by phone', async () => {
        respond({ bridge: 'plivo_mpc', needs_phone: true });
        join.mockResolvedValue({ data: { mode: 'takeover' } });
        show();
        fireEvent.click(await screen.findByRole('button', { name: /Take over/ }));
        const button = screen.getByRole('button', { name: 'Call me and join' }) as HTMLButtonElement;
        expect(button.disabled).toBe(true);
        fireEvent.change(screen.getByLabelText(/Your phone number/), { target: { value: '+919800000001' } });
        fireEvent.click(button);
        await waitFor(() =>
            expect(join).toHaveBeenCalledWith({
                path: { run_id: 41 },
                body: { mode: 'takeover', phone: '+919800000001' },
            }),
        );
        expect(openMic).not.toHaveBeenCalled();
        // The talking socket still opens, with no microphone: the page being
        // open is what keeps the call the supervisor's.
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        FakeSocket.all[0].emit({ type: 'live' });
        expect(FakeSocket.all[0].sent).toEqual([]);
    });
});

describe('on the call', () => {
    it('in a barge: let the agent answer, take over fully, hand back', async () => {
        respond({ mode: 'barge', by: 'priya', by_user_id: 1, mine: true });
        letAnswer.mockResolvedValue({ data: null });
        join.mockResolvedValue({ data: { mode: 'takeover' } });
        handBack.mockResolvedValue({ data: { mode: 'ai' } });
        show(ME_BARGING);
        fireEvent.click(await screen.findByRole('button', { name: 'Let the agent answer' }));
        await waitFor(() => expect(letAnswer).toHaveBeenCalledWith({ path: { run_id: 41 } }));
        fireEvent.click(screen.getByRole('button', { name: 'Take over fully' }));
        await waitFor(() => expect(join).toHaveBeenCalledWith({ path: { run_id: 41 }, body: { mode: 'takeover' } }));
        fireEvent.click(screen.getByRole('button', { name: /Hand back to the agent/ }));
        await waitFor(() => expect(handBack).toHaveBeenCalledWith({ path: { run_id: 41 } }));
        expect(screen.getByText(/the agent picks the call back up after 5 seconds/)).toBeTruthy();
    });

    it('says when the agent is answering, and offers no "let it answer" in a take-over', async () => {
        respond({ mode: 'barge', by: 'priya', by_user_id: 1, mine: true });
        const { rerender } = show({ ...ME_BARGING, agentAnswering: true });
        expect(await screen.findByText('The agent is answering. Speak to cut in.')).toBeTruthy();
        expect((screen.getByRole('button', { name: 'Let the agent answer' }) as HTMLButtonElement).disabled).toBe(true);
        respond({ mode: 'takeover', by: 'priya', by_user_id: 1, mine: true });
        rerender(<TakeoverControls runId={41} live={{ ...ME_BARGING, mode: 'takeover' }} ended={false} />);
        expect(await screen.findByText('You have taken over. The agent is silent.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Let the agent answer' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Switch to barge' })).toBeTruthy();
    });

    it('closes the microphone when the call goes back to the agent', async () => {
        respond();
        const mic = new FakeMic();
        openMic.mockResolvedValue(mic);
        join.mockResolvedValue({ data: { mode: 'takeover' } });
        const speaking: boolean[] = [];
        const { rerender } = show(AGENT_HAS_IT, (s) => speaking.push(s));
        fireEvent.click(await screen.findByRole('button', { name: /Take over/ }));
        respond({ mode: 'takeover', by: 'priya', by_user_id: 1, mine: true });
        fireEvent.click(screen.getByRole('button', { name: 'Turn on my microphone and join' }));
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        const live = { ...ME_BARGING, mode: 'takeover' as const };
        rerender(<TakeoverControls runId={41} live={live} ended={false} onSpeakingChange={(s) => speaking.push(s)} />);
        FakeSocket.all[0].emit({ type: 'live' });
        await waitFor(() => expect(speaking.at(-1)).toBe(true));
        // Recovered after a drop, or handed back by an admin.
        respond();
        rerender(<TakeoverControls runId={41} live={AGENT_HAS_IT} ended={false} onSpeakingChange={(s) => speaking.push(s)} />);
        await waitFor(() => expect(mic.closed).toBe(true));
        expect(FakeSocket.all[0].closed).toBe(true);
        await waitFor(() => expect(speaking.at(-1)).toBe(false));
    });
});

describe('somebody else on the call', () => {
    it('says who, and lets an admin hand it back', async () => {
        respond({ mode: 'takeover', by: 'user9', by_user_id: 9, mine: false, can_join: false, blocked: 'taken' });
        handBack.mockResolvedValue({ data: { mode: 'ai' } });
        show({ mode: 'takeover', by: 'user9', byUserId: 9, agentAnswering: false });
        expect(await screen.findByText('user9 has taken over this call.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Barge in/ })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Hand back to the agent' }));
        await waitFor(() => expect(handBack).toHaveBeenCalled());
    });
});

const PSTN_VOICE_OFF =
    'Speaking into phone calls from the browser is off until it is cleared with telecom counsel. You can take the call over: the agent goes quiet, you guide it by typing, and then hand the call back.';

describe('a phone call, while speaking into it from the browser is held back', () => {
    it('offers no barge, says why, and takes over without asking for the microphone', async () => {
        respond({ voice: false, can_barge: false, notice: PSTN_VOICE_OFF });
        join.mockResolvedValue({ data: { mode: 'takeover', by: 'priya', by_user_id: 1 } });
        show();
        expect(await screen.findByText(PSTN_VOICE_OFF)).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Barge in/ })).toBeNull();
        expect(screen.getByText('Silence the agent and guide it by typing.')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: /Take over/ }));
        expect(screen.getByText(SILENT_TAKEOVER_COPY.body)).toBeTruthy();
        expect(screen.queryByText(JOIN_WARNING)).toBeNull();
        respond({ voice: false, can_barge: false, notice: PSTN_VOICE_OFF, mode: 'takeover', by: 'priya', by_user_id: 1, mine: true });
        fireEvent.click(screen.getByRole('button', { name: 'Take over the call' }));
        await waitFor(() => expect(join).toHaveBeenCalledWith({ path: { run_id: 41 }, body: { mode: 'takeover' } }));
        expect(openMic).not.toHaveBeenCalled();
        // The talking socket still opens, to say the supervisor is there.
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        expect(await screen.findByText('You’ve taken over. The caller can’t hear you.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Switch to barge' })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Mute' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Hand back to the agent' })).toBeTruthy();
    });

    it('says when joining by phone cannot reach this call', async () => {
        const note =
            "Joining by phone works only for calls placed in a Plivo conference, and calls are not placed in one yet, so this call can't be joined by phone.";
        respond({ bridge: 'plivo_mpc', needs_phone: true, notice: note });
        show();
        expect(await screen.findByText(note)).toBeTruthy();
        expect(screen.getByRole('button', { name: /Barge in/ })).toBeTruthy();
    });
});
