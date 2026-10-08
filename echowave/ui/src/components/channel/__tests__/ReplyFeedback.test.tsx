/**
 * "Was this useful?" under Decibyl's replies (handoff 6; launch stream
 * controls).
 *
 * Guarded: Yes and Not quite save straight away; Not quite offers the five
 * reasons and saves each change; a failed save says so and never thanks;
 * the prompt can be closed; and only Decibyl's own answers are judgeable.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const give = vi.hoisted(() => vi.fn());
const mine = vi.hoisted(() => vi.fn());
const clientEvent = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ event_catalogue: false }));
vi.mock('@/client/sdk.gen', () => ({
    giveFeedbackApiV1FeedbackPost: give,
    myFeedbackApiV1FeedbackMineGet: mine,
    clientEventApiV1EventsClientPost: clientEvent,
}));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { isJudgeableReply, ReplyFeedback } from '../ReplyFeedback';

beforeEach(() => {
    give.mockReset();
    clientEvent.mockReset();
    flags.event_catalogue = false;
    window.localStorage.clear();
});

describe('the prompt', () => {
    it('saves Yes and thanks only once it is saved', async () => {
        give.mockResolvedValue({ data: { id: 1 } });
        const onAnswered = vi.fn();
        render(<ReplyFeedback eventId={7} onAnswered={onAnswered} />);
        fireEvent.click(screen.getByRole('button', { name: 'Yes' }));
        await waitFor(() => expect(onAnswered).toHaveBeenCalled());
        expect(give.mock.calls[0][0].body).toEqual({
            subject_kind: 'reply',
            subject_id: 7,
            verdict: 'yes',
            reasons: [],
        });
        expect(screen.getByText('Thanks, marked as useful.')).toBeTruthy();
    });

    it('offers the reasons after Not quite and saves each one', async () => {
        give.mockResolvedValue({ data: { id: 1 } });
        render(<ReplyFeedback eventId={7} onAnswered={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Not quite' }));
        await waitFor(() => expect(give).toHaveBeenCalledTimes(1));
        for (const label of ['Wrong', 'Not relevant', 'Too late', 'Too long', 'Wrong language']) {
            expect(screen.getByRole('button', { name: label })).toBeTruthy();
        }
        fireEvent.click(screen.getByRole('button', { name: 'Too long' }));
        await waitFor(() => expect(give).toHaveBeenCalledTimes(2));
        expect(give.mock.calls[1][0].body.reasons).toEqual(['too_long']);
        expect(screen.getByRole('button', { name: 'Too long' }).getAttribute('aria-pressed')).toBe('true');
    });

    it('says when it was not saved, and does not thank', async () => {
        give.mockResolvedValue({ error: { detail: 'boom' } });
        const onAnswered = vi.fn();
        render(<ReplyFeedback eventId={7} onAnswered={onAnswered} />);
        fireEvent.click(screen.getByRole('button', { name: 'Yes' }));
        await waitFor(() =>
            expect(screen.getByRole('alert').textContent).toBe('Your feedback was not saved. Try again.'),
        );
        expect(onAnswered).not.toHaveBeenCalled();
        expect(screen.queryByText('Thanks, marked as useful.')).toBeNull();
    });

    it('can be closed, and stays closed for that reply', () => {
        flags.event_catalogue = true;
        clientEvent.mockResolvedValue({ data: { event_id: 'x' } });
        const { unmount } = render(<ReplyFeedback eventId={9} onAnswered={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Close' }));
        expect(screen.queryByText('Was this useful?')).toBeNull();
        expect(clientEvent).toHaveBeenCalledWith({ body: { name: 'feedback_prompt_dismissed' } });
        unmount();
        render(<ReplyFeedback eventId={9} onAnswered={vi.fn()} />);
        expect(screen.queryByText('Was this useful?')).toBeNull();
    });

    it('shows what was already said instead of asking again', () => {
        render(
            <ReplyFeedback
                eventId={7}
                answer={{ verdict: 'not_quite', reasons: ['wrong'] }}
                onAnswered={vi.fn()}
            />,
        );
        expect(screen.queryByText('Was this useful?')).toBeNull();
        expect(screen.getByRole('button', { name: 'Wrong' }).getAttribute('aria-pressed')).toBe('true');
    });
});

describe('which replies', () => {
    const row = (over: Record<string, unknown>) =>
        ({
            id: 1,
            at: '2026-10-07T00:00:00Z',
            kind: 'message',
            actor: 'agent',
            summary: 'x',
            payload: { from: 'Decibyl', body: 'x' },
            is_deliverable: false,
            workflow_id: null,
            workflow_run_id: null,
            folder_id: null,
            ...over,
        }) as never;

    it("is Decibyl's own answers only", () => {
        expect(isJudgeableReply(row({}))).toBe(true);
        expect(isJudgeableReply(row({ actor: 'human' }))).toBe(false);
        expect(isJudgeableReply(row({ workflow_id: 3 }))).toBe(false);
        expect(isJudgeableReply(row({ payload: { from: 'Decibyl', quota: { kind: 'model_turns' } } }))).toBe(false);
        expect(isJudgeableReply(row({ payload: { from: 'Decibyl', action_event_id: 4 } }))).toBe(false);
    });
});
