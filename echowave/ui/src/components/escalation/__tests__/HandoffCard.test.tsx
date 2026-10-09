/**
 * The handoff card: what the person who picks up reads, and the three
 * buttons answered in place -- Accept, Decline, Hand back to AI with a note.
 * Where the carrier cannot move a caller back, the card says so instead.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
    get: vi.fn(),
    accept: vi.fn(),
    decline: vi.fn(),
    handBack: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/client/sdk.gen', () => ({
    getEscalationApiV1EscalationsEscalationUuidGet: api.get,
    acceptEscalationApiV1EscalationsEscalationUuidAcceptPost: api.accept,
    declineEscalationApiV1EscalationsEscalationUuidDeclinePost: api.decline,
    handBackEscalationApiV1EscalationsEscalationUuidHandBackPost: api.handBack,
}));

import { HandoffCard, stateLine } from '../HandoffCard';

const CARD = {
    caller: { name: 'Asha', number_masked: '…3210', verified: false, verification: 'known number, not verified' },
    intent: 'Order 1234 never arrived',
    fields: { order_id: '1234' },
    actions: [
        { tool: 'lookup_order', ok: true, result: 'shipped' },
        { tool: 'issue_credit', ok: false, result: 'failed' },
    ],
    reason: 'Asked for a person',
    reason_code: 'explicit_request',
    summary: 'Asha says order 1234 never arrived. The agent could not issue a credit.',
    language: 'hi-IN',
    consent: { ai_disclosed: true, recording_disclosed: true },
    transcript_url: '/workflow/7/run/9',
};

function escalation(overrides: Record<string, unknown> = {}) {
    return {
        escalation_uuid: 'e-1',
        state: 'bridged',
        reason_code: 'explicit_request',
        trigger: 'auto',
        attempts: [],
        handoff_card: CARD,
        human_response: null,
        can_hand_back: true,
        ...overrides,
    };
}

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
});

describe('HandoffCard', () => {
    it('shows who, why, what was done and the summary', async () => {
        api.get.mockResolvedValue({ data: escalation() });
        render(<HandoffCard escalationUuid="e-1" />);
        expect(await screen.findByText('Asha needs a person')).toBeTruthy();
        expect(screen.getByText('known number, not verified')).toBeTruthy();
        expect(screen.getByText(CARD.summary)).toBeTruthy();
        expect(screen.getByText('Order 1234 never arrived')).toBeTruthy();
        expect(screen.getByText(/issue credit/)).toBeTruthy();
        expect(screen.getByLabelText('failed')).toBeTruthy();
        expect(screen.getByText('speaking with an AI, the call is recorded')).toBeTruthy();
        expect(screen.getByRole('link', { name: 'Live transcript' }).getAttribute('href')).toBe('/workflow/7/run/9');
        expect(screen.getByTestId('handoff-state').textContent).toContain('The caller is with a person');
    });

    it('hands the caller back to the agent with a note', async () => {
        api.get.mockResolvedValue({ data: escalation() });
        api.handBack.mockResolvedValue({
            data: escalation({ state: 'completed', outcome_note: 'Refund approved', can_hand_back: false }),
        });
        render(<HandoffCard escalationUuid="e-1" />);
        fireEvent.click(await screen.findByRole('button', { name: /Hand back to AI/ }));
        fireEvent.change(screen.getByLabelText(/What was decided/), { target: { value: 'Refund approved' } });
        fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
        await waitFor(() =>
            expect(api.handBack).toHaveBeenCalledWith({ path: { escalation_uuid: 'e-1' }, body: { note: 'Refund approved' } }),
        );
        expect(await screen.findByText('Refund approved')).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Hand back to AI/ })).toBeNull();
    });

    it('says so where the carrier cannot hand a caller back', async () => {
        api.get.mockResolvedValue({ data: escalation({ can_hand_back: false }) });
        render(<HandoffCard escalationUuid="e-1" />);
        expect(await screen.findByTestId('handoff-no-hand-back')).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Hand back to AI/ })).toBeNull();
    });

    it('accepts and declines in place, and shows a refusal in words', async () => {
        api.get.mockResolvedValue({ data: escalation({ state: 'dialling' }) });
        api.accept.mockResolvedValue({ data: escalation({ state: 'dialling', human_response: 'accepted' }) });
        api.decline.mockResolvedValue({ error: { detail: 'That handover is not here.' } });
        render(<HandoffCard escalationUuid="e-1" />);
        fireEvent.click(await screen.findByRole('button', { name: /Accept/ }));
        await waitFor(() => expect(screen.queryByRole('button', { name: /Accept/ })).toBeNull());
        fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.getByRole('alert').textContent).toContain('That handover is not here.');
    });

    it('draws from the thread row before the fetch answers', () => {
        api.get.mockReturnValue(new Promise(() => {}));
        render(<HandoffCard escalationUuid="e-1" initialCard={CARD} initialState="dialling" />);
        expect(screen.getByText('Asha needs a person')).toBeTruthy();
        expect(screen.getByTestId('handoff-state').textContent).toContain('Ringing the team');
    });
});

describe('stateLine', () => {
    it('says why a handover did not connect, and what happened instead', () => {
        expect(stateLine('failed', 'machine', 'callback')).toBe('Not connected: it reached voicemail · a callback was booked');
        // An unknown reason shows as itself rather than as nothing.
        expect(stateLine('failed', 'carrier_strike', null)).toBe('Not connected: carrier_strike');
    });
});
