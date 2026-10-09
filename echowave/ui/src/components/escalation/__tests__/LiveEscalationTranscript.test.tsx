/**
 * The live view the handoff card links to: where the handover stands and the
 * latest lines, refreshed while the call lasts, and no more polling once it
 * has ended.
 */

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/client/sdk.gen', () => ({
    getEscalationApiV1EscalationsEscalationUuidGet: api.get,
}));

import { LiveEscalationTranscript } from '../LiveEscalationTranscript';

function escalation(lines: string[], state = 'dialling') {
    return {
        data: {
            escalation_uuid: 'e-1',
            state,
            reason_code: 'policy',
            trigger: 'auto',
            attempts: [],
            handoff_card: {
                reason: 'Always goes to a person',
                team: 'Fraud desk',
                summary: 'Asha reports a card fraud.',
                live_transcript: lines,
            },
        },
    };
}

beforeEach(() => api.get.mockReset());

describe('LiveEscalationTranscript', () => {
    it('shows the state, the team and the latest lines, and refreshes while the call is live', async () => {
        api.get
            .mockResolvedValueOnce(escalation(['Caller: someone used my card']))
            .mockResolvedValue(escalation(['Caller: someone used my card', 'Caller: hello?'], 'bridged'));
        let live = true;
        const isRunLive = vi.fn(async () => live);
        render(<LiveEscalationTranscript escalationUuid="e-1" isRunLive={isRunLive} refreshMs={10} />);

        expect(await screen.findByText('Caller: someone used my card')).toBeTruthy();
        expect(screen.getByTestId('live-escalation-state').textContent).toContain('Fraud desk');
        expect(await screen.findByText('Caller: hello?')).toBeTruthy();
        expect(screen.getByTestId('live-escalation-state').textContent).toContain('The caller is with a person');

        live = false;
        await waitFor(() => expect(isRunLive).toHaveBeenCalled());
        const calls = api.get.mock.calls.length;
        await new Promise((r) => setTimeout(r, 60));
        // Once the run has ended at most the tick in flight finishes.
        expect(api.get.mock.calls.length).toBeLessThanOrEqual(calls + 1);
    });

    it('says when nothing has been said yet, and a refusal in words', async () => {
        api.get.mockResolvedValue({ error: { detail: 'That handover is not here.' } });
        render(<LiveEscalationTranscript escalationUuid="e-1" isRunLive={async () => false} />);
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.getByRole('alert').textContent).toContain('That handover is not here.');
        expect(screen.getByText('Nothing said yet.')).toBeTruthy();
    });
});
