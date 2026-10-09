/**
 * "Live now": the calls in progress where calls already show, opened in
 * place. Nothing at all while the feature is off; the way past a wall (the
 * workspace switch) offered where the wall is.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const list = vi.hoisted(() => vi.fn());
const setSettings = vi.hoisted(() => vi.fn());
const detail = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ live_supervision: true }) as Record<string, boolean>);

vi.mock('@/client/sdk.gen', () => ({
    listLiveCallsApiV1LiveCallsGet: list,
    setLiveSettingsApiV1LiveCallsSettingsPut: setSettings,
    liveCallDetailApiV1LiveCallsRunIdGet: detail,
    proposeConsentFixApiV1LiveCallsRunIdConsentFixPost: vi.fn(),
    whisperToCallApiV1LiveCallsRunIdWhisperPost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({
    useAuth: () => ({ user: { id: 1 }, loading: false, getAccessToken: async () => 't' }),
}));
vi.mock('@/lib/features', () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock('@/client/client.gen', () => ({ client: { getConfig: () => ({ baseUrl: 'http://api.test' }) } }));

import { LiveNowStrip } from '../LiveNowStrip';

const call = (over: Record<string, unknown> = {}) => ({
    run_id: 41,
    workflow_id: 3,
    agent_name: 'Front desk',
    direction: 'inbound',
    started_at: new Date(Date.now() - 125_000).toISOString(),
    duration_seconds: 125,
    step: 'Booking',
    caller: '•••• 5678',
    caller_masked: true,
    can_listen: true,
    blocked: null,
    ...over,
});

const listing = (calls: unknown[], over: Record<string, unknown> = {}) => ({
    data: { calls, allow_listening: true, can_change_setting: false, more: false, ...over },
});

class SilentSocket {
    static last: SilentSocket | null = null;
    binaryType = '';
    onmessage: ((e: { data: unknown }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    constructor(public url: string) {
        SilentSocket.last = this;
    }
    close() {}
}

beforeEach(() => {
    list.mockReset();
    setSettings.mockReset();
    detail.mockReset();
    flags.live_supervision = true;
    vi.stubGlobal('WebSocket', SilentSocket);
});
afterEach(() => vi.unstubAllGlobals());

describe('the strip', () => {
    it('draws nothing and asks nothing while the feature is off', async () => {
        flags.live_supervision = false;
        const { container } = render(<LiveNowStrip />);
        await act(async () => {});
        expect(container.innerHTML).toBe('');
        expect(list).not.toHaveBeenCalled();
    });

    it('draws nothing when no call is in progress', async () => {
        list.mockResolvedValue(listing([]));
        const { container } = render(<LiveNowStrip />);
        await waitFor(() => expect(list).toHaveBeenCalled());
        expect(container.innerHTML).toBe('');
    });

    it("shows each call: agent, the caller's masked number, time on the call, step", async () => {
        list.mockResolvedValue(listing([call()]));
        render(<LiveNowStrip />);
        expect(await screen.findByText('Front desk · •••• 5678')).toBeTruthy();
        expect(screen.getByText('Booking')).toBeTruthy();
        expect(screen.getByText(/^2:0\d$/)).toBeTruthy();
        expect(screen.getByRole('region', { name: 'Live now' }).textContent).toContain('Live now · 1');
    });

    it("on an agent's thread asks for that agent's calls only and leaves its name out", async () => {
        list.mockResolvedValue(listing([call({ caller: null, direction: 'web', caller_masked: false })]));
        render(<LiveNowStrip workflowId={3} />);
        expect(await screen.findByText('Web call')).toBeTruthy();
        expect(list).toHaveBeenCalledWith({ query: { workflow_id: 3 } });
    });

    it('opens the listen panel in place, under the strip', async () => {
        list.mockResolvedValue(listing([call()]));
        detail.mockResolvedValue({
            data: { call: call(), transcript: [], consent: { mentions_monitoring: true, warning: null } },
        });
        render(<LiveNowStrip />);
        fireEvent.click(await screen.findByRole('button', { name: /Front desk/ }));
        expect(await screen.findByRole('region', { name: 'Listening to Front desk' })).toBeTruthy();
        expect(await screen.findByLabelText('Live transcript')).toBeTruthy();
        expect(SilentSocket.last?.url).toBe('ws://api.test/api/v1/ws/live-calls/41');
    });

    it('names the workspace switch and lets an admin turn it on right there', async () => {
        list.mockResolvedValueOnce(
            listing([call({ can_listen: false, blocked: 'setting_off' })], {
                allow_listening: false,
                can_change_setting: true,
            }),
        );
        list.mockResolvedValue(listing([call()]));
        setSettings.mockResolvedValue({ data: { allow_listening: true } });
        detail.mockReturnValue(new Promise(() => {}));
        render(<LiveNowStrip />);
        fireEvent.click(await screen.findByRole('button', { name: /Front desk/ }));
        expect(screen.getByText('Live listening is off for this workspace.')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Turn on live listening' }));
        await waitFor(() => expect(setSettings).toHaveBeenCalledWith({ body: { allow_listening: true } }));
        await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
        expect(await screen.findByText('Opening the call…')).toBeTruthy();
    });

    it('tells a member who cannot listen who can, with no switch to press', async () => {
        list.mockResolvedValue(listing([call({ can_listen: false, blocked: 'not_permitted' })]));
        render(<LiveNowStrip />);
        fireEvent.click(await screen.findByRole('button', { name: /Front desk/ }));
        expect(screen.getByRole('status').textContent).toContain('Only the agent’s owner or a workspace admin');
        expect(screen.queryByRole('button', { name: 'Turn on live listening' })).toBeNull();
    });

    it('says when the list could not load instead of looking empty', async () => {
        list.mockResolvedValue({ error: { detail: 'Server unavailable' } });
        render(<LiveNowStrip />);
        expect((await screen.findByRole('alert')).textContent).toContain('Server unavailable');
    });
});
