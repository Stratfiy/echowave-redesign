/**
 * A caller handed to a person shows on the agent's thread as the handoff
 * card (escalation v2), with its buttons -- and, with the switch off, the row
 * is not turned into a card at all.
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const flags = vi.hoisted(() => ({ on: new Set<string>() }));
const timeline = vi.hoisted(() => vi.fn());
const getEscalation = vi.hoisted(() => vi.fn());

vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => flags.on.has(name),
    useFeaturesSettled: () => true,
}));
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: vi.fn(async () => ({ data: { text: '' } })),
    threadChipsApiV1TimelineChipsGet: vi.fn(async () => ({ data: { chips: [] } })),
    postMessageApiV1TimelineMessagePost: vi.fn(),
    getEscalationApiV1EscalationsEscalationUuidGet: getEscalation,
    acceptEscalationApiV1EscalationsEscalationUuidAcceptPost: vi.fn(),
    declineEscalationApiV1EscalationsEscalationUuidDeclinePost: vi.fn(),
    handBackEscalationApiV1EscalationsEscalationUuidHandBackPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { ChannelStream } from '../ChannelStream';

const ROW = {
    id: 11,
    at: '2026-10-09T06:00:00Z',
    kind: 'escalated',
    actor: 'agent',
    summary: 'Asha needs a person: Asked for a person',
    payload: {
        escalation_uuid: 'e-1',
        state: 'dialling',
        handoff: { caller: { name: 'Asha' }, reason: 'Asked for a person', summary: 'Order 1234 never came.' },
    },
    is_deliverable: false,
    workflow_id: 3,
    workflow_run_id: 9,
    folder_id: null,
};

beforeEach(() => {
    flags.on.clear();
    timeline.mockReset();
    timeline.mockResolvedValue({ data: { events: [ROW], next_before_at: null, next_before_id: null } });
    getEscalation.mockReset();
    getEscalation.mockResolvedValue({
        data: {
            escalation_uuid: 'e-1',
            state: 'dialling',
            reason_code: 'explicit_request',
            trigger: 'auto',
            attempts: [],
            handoff_card: ROW.payload.handoff,
            can_hand_back: false,
        },
    });
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
});

describe('a handover on the thread', () => {
    it('is the handoff card with Accept and Decline when escalation v2 is on', async () => {
        flags.on.add('escalation_v2');
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        expect(await screen.findByTestId('handoff-card')).toBeTruthy();
        expect(screen.getByText('Order 1234 never came.')).toBeTruthy();
        expect(await screen.findByRole('button', { name: /Accept/ })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Decline' })).toBeTruthy();
    });

    it('is not a card while the switch is off', async () => {
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findAllByText(/Asha needs a person/);
        expect(screen.queryByTestId('handoff-card')).toBeNull();
        expect(getEscalation).not.toHaveBeenCalled();
    });
});
