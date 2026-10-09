/**
 * "Call me when it's done" in the thread: the chip (its text is the ask, so
 * tapping it is the same as typing it), Decibyl's notice about the call, and
 * the number asked for right there when none is on file. With the flag off,
 * nothing new is offered.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const chips = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
const ask = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ call_when_done: true }));
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: vi.fn().mockResolvedValue({ data: { text: '' } }),
    decideApiV1TimelineDecidePost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    translateTextApiV1TranslatePost: vi.fn(),
    threadChipsApiV1TimelineChipsGet: chips,
    postMessageApiV1TimelineMessagePost: post,
    giveFeedbackApiV1FeedbackPost: vi.fn(),
    myFeedbackApiV1FeedbackMineGet: vi.fn().mockResolvedValue({ data: { answers: {}, reasons: [] } }),
    clientEventApiV1EventsClientPost: vi.fn(),
    askToBeCalledApiV1CallWhenDonePost: ask,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { ChannelStream } from '../ChannelStream';

function notice(id: number, body: string, info: Record<string, unknown>) {
    return {
        id,
        at: '2026-10-07T06:00:00Z',
        kind: 'message',
        actor: 'agent',
        summary: body,
        payload: { from: 'Decibyl', body, private_to: 1, call_when_done: info },
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    };
}

function events(list: unknown[]) {
    timeline.mockResolvedValue({ data: { events: list, next_before_at: null, next_before_id: null } });
}

beforeEach(() => {
    timeline.mockReset();
    chips.mockReset();
    post.mockReset();
    ask.mockReset();
    flags.call_when_done = true;
    chips.mockResolvedValue({ data: { chips: [] } });
    post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
    events([]);
    Element.prototype.scrollIntoView = vi.fn();
});

describe('the Call me when done chip', () => {
    it('is shown with a phone and sends the ask itself on this thread', async () => {
        chips.mockResolvedValue({ data: { chips: [{ kind: 'call_when_done', text: 'Call me when done' }] } });
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        const chip = await screen.findByTestId('chip-call-when-done');
        expect(chip.textContent).toBe('Call me when done');
        expect(chip.querySelector('svg')).toBeTruthy();
        fireEvent.click(chip);
        await waitFor(() =>
            expect(post).toHaveBeenCalledWith({
                body: { assistant: true, thread_id: 't1', text: 'Call me when done' },
            }),
        );
    });

    it('is not drawn when the server does not offer it', async () => {
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        await waitFor(() => expect(chips).toHaveBeenCalled());
        expect(screen.queryByTestId('chip-call-when-done')).toBeNull();
    });
});

describe("Decibyl's notice about the call", () => {
    it('says when it will ring, outside calling hours included', async () => {
        events([
            notice(41, "Done: Deploy the site. I'll call you at 9:00, outside calling hours now.", {
                call_id: 3,
                state: 'queued',
            }),
        ]);
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        const card = await screen.findByTestId('call-when-done-notice');
        expect(card.getAttribute('data-state')).toBe('queued');
        expect(card.textContent).toContain("I'll call you at 9:00, outside calling hours now.");
        expect(screen.queryByLabelText('Number to ring')).toBeNull();
    });

    it('asks for the number in the thread and puts it on a card', async () => {
        events([
            notice(42, 'I\'ll call you when it is done, between 9:00 and 21:00. Which number should I ring?', {
                callback_id: 9,
                state: 'pending',
                needs_number: true,
            }),
        ]);
        ask.mockResolvedValue({ data: { callback_id: 9, card_event_id: 77 } });
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        const field = await screen.findByLabelText('Number to ring');
        fireEvent.change(field, { target: { value: '98765 43210' } });
        fireEvent.click(screen.getByRole('button', { name: 'Show it on a card' }));
        await waitFor(() =>
            expect(ask).toHaveBeenCalledWith({ body: { thread_id: 't1', phone: '98765 43210' } }),
        );
        expect(await screen.findByText('Confirm the number on the card in this thread.')).toBeTruthy();
    });

    it('says why a number was refused, from the error body', async () => {
        events([notice(43, 'Which number should I ring?', { state: 'pending', needs_number: true })]);
        ask.mockResolvedValue({ error: { detail: [{ msg: 'That does not look like a phone number.', loc: ['body', 'phone'] }] } });
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        fireEvent.change(await screen.findByLabelText('Number to ring'), { target: { value: '12' } });
        fireEvent.click(screen.getByRole('button', { name: 'Show it on a card' }));
        expect((await screen.findByRole('alert')).textContent).toContain('That does not look like a phone number.');
    });

    it('offers nothing new while the flag is off', async () => {
        flags.call_when_done = false;
        events([notice(44, 'Which number should I ring?', { state: 'pending', needs_number: true })]);
        render(<ChannelStream assistant chatShell threadId="t1" botNames={{}} />);
        await screen.findByTestId('call-when-done-notice');
        expect(screen.queryByLabelText('Number to ring')).toBeNull();
    });
});
