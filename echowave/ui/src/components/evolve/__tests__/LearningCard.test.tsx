/**
 * The learning card: what a lesson changes, the evidence and how it tested,
 * and nothing published until a person presses. A remembered draft is
 * edited in place before it is saved.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ settle: vi.fn(), edit: vi.fn() }));

vi.mock('@/client/sdk.gen', () => ({
    settleCardApiV1EvolveCardsSettlePost: api.settle,
    editVersionApiV1EvolveVersionsVersionIdPut: api.edit,
}));

import type { TimelineEvent } from '@/client/types.gen';

import { heading, LearningCard } from '../LearningCard';

function card(payload: Record<string, unknown>): TimelineEvent {
    return {
        id: 42,
        kind: 'skill_lesson',
        actor: 'agent',
        summary: 'x',
        at: '2026-10-09T10:00:00Z',
        payload,
    } as unknown as TimelineEvent;
}

const OFFER = {
    type: 'offer',
    version_id: 9,
    slug: 'brand-voice',
    title: 'book appointments',
    version: 2,
    changes: [
        { op: 'add', text: "Ask for the caller's timezone before booking" },
        { op: 'remove', text: 'Book first, ask later' },
    ],
    evidence: [
        { id: 1, summary: 'Correction · Ask for the timezone first · 09 Oct' },
        { id: 2, summary: 'Task · calendar_create failed · 08 Oct' },
    ],
    evaluation: {
        passed: true,
        related: { n: 3, baseline_passed: 0, candidate_passed: 3, regressions: [] },
        unrelated: { n: 4, baseline_passed: 4, candidate_passed: 4, regressions: [] },
    },
    cost: { model_calls: 14, tokens: 900 },
};

beforeEach(() => {
    vi.clearAllMocks();
    api.settle.mockImplementation(async ({ body }: { body: { action: string } }) => ({
        data: { event_id: 42, payload: { ...OFFER, decided: { action: body.action, by: 1 } } },
    }));
    api.edit.mockResolvedValue({ data: { id: 9 } });
});

describe('LearningCard', () => {
    it('says what it learned, from what, and how it tested', () => {
        render(<LearningCard event={card(OFFER)} />);
        expect(screen.getByText("I've learned a better way to book appointments")).toBeTruthy();
        expect(screen.getByText(/\+ Ask for the caller's timezone before booking/)).toBeTruthy();
        expect(screen.getByText(/− Book first, ask later/)).toBeTruthy();
        const evaluation = screen.getByTestId('learning-evaluation').textContent ?? '';
        expect(evaluation).toMatch(/3 of 3 right,\s*against 0 before/);
        expect(evaluation).toMatch(/none got worse \(4 checked\)/);
        expect(evaluation).toMatch(/14 model calls/);
        expect(screen.getByText('Learned from 2 things that happened')).toBeTruthy();
    });

    it('publishes only when the person presses, and then says so', async () => {
        const settled = vi.fn();
        render(<LearningCard event={card(OFFER)} onSettled={settled} />);
        expect(api.settle).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
        await waitFor(() =>
            expect(api.settle).toHaveBeenCalledWith({ body: { event_id: 42, action: 'publish' } }),
        );
        expect(settled.mock.calls[0][0].payload.decided.action).toBe('publish');
    });

    it('shows a decided card as decided, with no buttons', () => {
        render(<LearningCard event={card({ ...OFFER, decided: { action: 'discard', by: 1 } })} />);
        expect(screen.getByTestId('learning-decided').textContent).toMatch('Not taken');
        expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
    });

    it('shows a refusal in place', async () => {
        api.settle.mockResolvedValue({ error: { detail: 'Only an admin of this workspace can publish a change to it.' } });
        render(<LearningCard event={card(OFFER)} />);
        fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
        expect((await screen.findByRole('alert')).textContent).toMatch(/Only an admin/);
    });

    it('lets a remembered draft be edited before it is saved', async () => {
        const remembered = {
            type: 'remembered',
            version_id: 9,
            title: 'Weekly client update',
            content: {
                title: 'Weekly client update',
                description: 'Write the Friday update',
                steps: ['Read the tasks', 'List what shipped'],
            },
            left_out: ['wont_do: “call the client after 9pm” is about calling hours and do-not-call'],
        };
        render(<LearningCard event={card(remembered)} />);
        expect(screen.getByText('Remembered as your way: Weekly client update')).toBeTruthy();
        expect(screen.getByTestId('left-out').textContent).toMatch(/stay in Decibyl's own rules/);
        fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
        fireEvent.change(screen.getByLabelText('Steps'), {
            target: { value: 'Read the tasks\nList what shipped\nName the next step' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save as a skill' }));
        await waitFor(() => expect(api.edit).toHaveBeenCalled());
        expect(api.edit.mock.calls[0][0].body.content.steps).toEqual([
            'Read the tasks',
            'List what shipped',
            'Name the next step',
        ]);
        await waitFor(() =>
            expect(api.settle).toHaveBeenCalledWith({ body: { event_id: 42, action: 'publish' } }),
        );
    });

    it('offers a rollback when later tasks went worse', () => {
        render(
            <LearningCard
                event={card({
                    type: 'disable',
                    title: 'book appointments',
                    version: 2,
                    related_outcomes: { before: { rate: 0.8, n: 5 }, after: { rate: 0.2, n: 5 } },
                })}
            />,
        );
        expect(screen.getByText('book appointments has done worse since version 2')).toBeTruthy();
        expect(screen.getByText(/80% of the time before and 20% since/)).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Roll back' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Keep it' })).toBeTruthy();
    });

    it('names the agent on an add card', () => {
        expect(heading({ type: 'attach', title: 'Brand Voice', agent_name: 'Front desk' })).toBe(
            'Add Brand Voice to Front desk?',
        );
    });
});
