/**
 * Who an agent hands callers to, on the agent's own page: nothing while the
 * switch is off; with it on, the policy loads, saves into the draft, and a
 * mistake comes back in words.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const flags = vi.hoisted(() => ({ on: new Set<string>() }));
const api = vi.hoisted(() => ({ get: vi.fn(), save: vi.fn() }));

vi.mock('@/lib/features', () => ({ useFeature: (name: string) => flags.on.has(name) }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/client/sdk.gen', () => ({
    getEscalationPolicyApiV1EscalationsPolicyWorkflowIdGet: api.get,
    saveEscalationPolicyApiV1EscalationsPolicyWorkflowIdPut: api.save,
}));

import { EscalationSettingsCard, formToHours, hoursToForm } from '../EscalationSettingsCard';

const POLICY = {
    transfer_numbers: [{ number: '+919876543210', name: 'Priya' }],
    transfer_hours: { enabled: false, timezone: 'Asia/Kolkata', slots: [] },
    always_transfer_topics: ['emergency', 'fraud', 'legal_threat'],
    custom_topics: [],
    refund_limit: null,
    max_ai_attempts: 2,
};
const TOPICS = [
    { key: 'emergency', label: 'Emergencies and safety' },
    { key: 'fraud', label: 'Fraud or a scam' },
    { key: 'legal_threat', label: 'Legal threats and formal complaints' },
    { key: 'refund_over_limit', label: 'Refunds over the limit' },
    { key: 'regulated_advice', label: 'Regulated advice (money, insurance, medical)' },
];

beforeEach(() => {
    flags.on.clear();
    api.get.mockReset();
    api.save.mockReset();
    api.get.mockResolvedValue({ data: { workflow_id: 7, policy: POLICY, topics: TOPICS, unpublished: false } });
});

describe('EscalationSettingsCard', () => {
    it('is absent while escalation v2 is off', () => {
        const { container } = render(<EscalationSettingsCard workflowId={7} />);
        expect(container.innerHTML).toBe('');
        expect(api.get).not.toHaveBeenCalled();
    });

    it('saves numbers, hours, topics and tries into the draft', async () => {
        flags.on.add('escalation_v2');
        api.save.mockResolvedValue({
            data: { workflow_id: 7, policy: { ...POLICY, max_ai_attempts: 3 }, topics: TOPICS, unpublished: true },
        });
        render(<EscalationSettingsCard workflowId={7} />);
        expect(await screen.findByText(/Rings Priya/)).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Change' }));
        fireEvent.click(screen.getByLabelText('Refunds over the limit'));
        fireEvent.change(screen.getByLabelText('Refund limit'), { target: { value: '5000' } });
        fireEvent.click(screen.getByLabelText(/Only at set hours/));
        fireEvent.change(screen.getByLabelText('Tries before a person takes over'), { target: { value: '3' } });
        fireEvent.change(screen.getByLabelText('Your own phrases'), { target: { value: 'cancel my membership, ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save to draft' }));
        await waitFor(() => expect(api.save).toHaveBeenCalled());
        const body = api.save.mock.calls[0][0].body.policy;
        expect(body.always_transfer_topics).toContain('refund_over_limit');
        expect(body.refund_limit).toBe(5000);
        expect(body.max_ai_attempts).toBe(3);
        expect(body.custom_topics).toEqual(['cancel my membership']);
        expect(body.transfer_hours.enabled).toBe(true);
        expect(body.transfer_hours.slots).toHaveLength(5);
        expect(await screen.findByTestId('escalation-unpublished')).toBeTruthy();
    });

    it('shows a refusal in words', async () => {
        flags.on.add('escalation_v2');
        api.save.mockResolvedValue({ error: { detail: 'transfer_numbers: not a phone number' } });
        render(<EscalationSettingsCard workflowId={7} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Change' }));
        fireEvent.click(screen.getByRole('button', { name: 'Save to draft' }));
        expect((await screen.findByRole('alert')).textContent).toContain('not a phone number');
    });
});

describe('hours on the form', () => {
    it('round-trips a weekday window', () => {
        const form = hoursToForm({
            enabled: true,
            timezone: 'Asia/Kolkata',
            slots: [0, 1, 2].map((d) => ({ day_of_week: d, start_time: '10:00', end_time: '17:00' })),
        });
        expect(form).toEqual({ enabled: true, days: [0, 1, 2], start: '10:00', end: '17:00' });
        expect(formToHours(form).slots).toHaveLength(3);
        expect(formToHours({ ...form, enabled: false }).slots).toEqual([]);
    });
});
