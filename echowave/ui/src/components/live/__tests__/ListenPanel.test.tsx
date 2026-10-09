/**
 * The listen panel: the call's words as they are said, both sides labelled;
 * whispers marked as never spoken; sound only when asked for and never the
 * microphone; the consent line with its fix as a card in place.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const detail = vi.hoisted(() => vi.fn());
const whisper = vi.hoisted(() => vi.fn());
const consentFix = vi.hoisted(() => vi.fn());

vi.mock('@/client/sdk.gen', () => ({
    liveCallDetailApiV1LiveCallsRunIdGet: detail,
    whisperToCallApiV1LiveCallsRunIdWhisperPost: whisper,
    proposeConsentFixApiV1LiveCallsRunIdConsentFixPost: consentFix,
    setLiveSettingsApiV1LiveCallsSettingsPut: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: async () => 'tok' }),
}));
vi.mock('@/client/client.gen', () => ({ client: { getConfig: () => ({ baseUrl: 'http://api.test' }) } }));

import { ListenPanel } from '../ListenPanel';

class FakeSocket {
    static all: FakeSocket[] = [];
    binaryType = '';
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onerror: (() => void) | null = null;
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

const CALL = {
    run_id: 41,
    workflow_id: 3,
    agent_name: 'Front desk',
    direction: 'inbound',
    started_at: new Date().toISOString(),
    duration_seconds: 5,
    step: 'Greeting',
    caller: '+919812345678',
    caller_masked: false,
    can_listen: true,
    blocked: null,
};

const WARNING = "Callers aren't told calls may be monitored — add it to your greeting";

function open(consent: { mentions_monitoring: boolean; warning: string | null } = { mentions_monitoring: true, warning: null }) {
    detail.mockResolvedValue({
        data: {
            call: CALL,
            transcript: [{ type: 'line', seq: 1, line: 1, speaker: 'agent', text: 'Hello, Asha Dental.', final: true }],
            consent,
        },
    });
    return render(<ListenPanel call={CALL} canChangeSetting={false} onClose={() => {}} />);
}

const getUserMedia = vi.fn();

beforeEach(() => {
    FakeSocket.all = [];
    detail.mockReset();
    whisper.mockReset();
    consentFix.mockReset();
    getUserMedia.mockReset();
    vi.stubGlobal('WebSocket', FakeSocket);
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia }, configurable: true });
});
afterEach(() => vi.unstubAllGlobals());

describe('listening', () => {
    it('streams both sides, labelled, interim words lighter until final', async () => {
        open();
        expect(await screen.findByText('Hello, Asha Dental.')).toBeTruthy();
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        const ws = FakeSocket.all[0];
        expect(ws.url).toBe('ws://api.test/api/v1/ws/live-calls/41');
        expect(ws.protocols).toEqual(['decibyl.auth', 'bearer.tok']);
        ws.emit({ type: 'hello', transcript: [] });
        ws.emit({ type: 'line', seq: 2, line: 2, speaker: 'caller', text: 'I want', final: false });
        const interim = await screen.findByText('I want');
        expect(interim.className).toContain('text-muted-foreground');
        ws.emit({ type: 'line', seq: 3, line: 2, speaker: 'caller', text: 'I want a cleaning', final: true });
        const final = await screen.findByText('I want a cleaning');
        expect(final.className).not.toContain('text-muted-foreground');
        expect(screen.queryByText('I want')).toBeNull();
        const labels = screen.getAllByText(/^(Agent|Caller)$/).map((n) => n.textContent);
        expect(labels).toEqual(['Agent', 'Caller']);
        // Receive-only: nothing is ever sent up the socket.
        expect(ws.sent).toEqual([]);
    });

    it('marks a whisper as never spoken to the caller', async () => {
        open();
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        FakeSocket.all[0].emit({ type: 'whisper', seq: 4, id: 'w', by: 'priya', text: 'Offer 5 pm', urgent: false });
        expect(await screen.findByText('Whisper from priya · not spoken to the caller')).toBeTruthy();
        expect(screen.getByText('Offer 5 pm')).toBeTruthy();
    });

    it('asks for sound only when told to, and never for the microphone', async () => {
        open();
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        vi.stubGlobal(
            'AudioContext',
            class {
                state = 'running';
                currentTime = 0;
                destination = {};
                resume = async () => {};
                close = async () => {};
                createBuffer() {
                    return { getChannelData: () => new Float32Array(1), duration: 0.02 };
                }
                createBufferSource() {
                    return { connect() {}, start() {}, stop() {} };
                }
            },
        );
        fireEvent.click(screen.getByRole('button', { name: 'Hear the call' }));
        await waitFor(() => expect(FakeSocket.all).toHaveLength(2));
        expect(FakeSocket.all[1].url).toBe('ws://api.test/api/v1/ws/live-calls/41?audio=1');
        expect(FakeSocket.all[0].closed).toBe(true);
        expect(getUserMedia).not.toHaveBeenCalled();
    });

    it('sends a whisper, urgent when ticked, and says what happens next', async () => {
        whisper.mockResolvedValue({ data: { id: 'w', at: 'now', urgent: true, text: 'Stop' } });
        open();
        const box = await screen.findByLabelText(/Whisper to the agent/);
        fireEvent.change(box, { target: { value: 'Tell them the clinic closes at 6.' } });
        fireEvent.click(screen.getByLabelText(/Urgent/));
        fireEvent.click(screen.getByRole('button', { name: /Send whisper/ }));
        await waitFor(() =>
            expect(whisper).toHaveBeenCalledWith({
                path: { run_id: 41 },
                body: { text: 'Tell them the clinic closes at 6.', urgent: true },
            }),
        );
        expect(await screen.findByText('Sent. The agent is answering with it now.')).toBeTruthy();
        expect((box as HTMLTextAreaElement).value).toBe('');
    });

    it('says why a whisper did not go', async () => {
        whisper.mockResolvedValue({ error: { detail: 'This call has ended.' } });
        open();
        fireEvent.change(await screen.findByLabelText(/Whisper to the agent/), { target: { value: 'x' } });
        fireEvent.click(screen.getByRole('button', { name: /Send whisper/ }));
        expect((await screen.findByRole('alert')).textContent).toBe('This call has ended.');
    });

    it('closes the whisper box when the call ends', async () => {
        open();
        await waitFor(() => expect(FakeSocket.all).toHaveLength(1));
        FakeSocket.all[0].emit({ type: 'ended', seq: 9 });
        expect(await screen.findByText('The call has ended.')).toBeTruthy();
        expect((screen.getByLabelText(/Whisper to the agent/) as HTMLTextAreaElement).disabled).toBe(true);
    });
});

describe('consent', () => {
    it('warns when the greeting never mentions monitoring, and proposes the change as a card here', async () => {
        consentFix.mockResolvedValue({
            data: {
                id: 77,
                at: '2026-10-09T06:00:00Z',
                kind: 'edit_proposed',
                actor: 'agent',
                summary: "Proposed a change to Start's greeting",
                payload: {
                    step: 'Start',
                    why: 'Tell callers the call may be monitored, before anyone listens in.',
                    greetings: [
                        { step: 'Start', old: 'Hello.', new: 'Hello. This call may be monitored by our team.' },
                    ],
                },
                is_deliverable: false,
                workflow_id: 3,
                workflow_run_id: null,
                folder_id: null,
            },
        });
        open({ mentions_monitoring: false, warning: WARNING });
        expect(await screen.findByText(WARNING)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Propose a greeting change' }));
        await waitFor(() => expect(consentFix).toHaveBeenCalledWith({ path: { run_id: 41 } }));
        expect(await screen.findByRole('button', { name: 'Publish' })).toBeTruthy();
        expect(screen.getByText(/This call may be monitored by our team/)).toBeTruthy();
    });

    it('says nothing when callers are already told', async () => {
        open();
        await screen.findByText('Hello, Asha Dental.');
        expect(screen.queryByRole('button', { name: 'Propose a greeting change' })).toBeNull();
    });
});
