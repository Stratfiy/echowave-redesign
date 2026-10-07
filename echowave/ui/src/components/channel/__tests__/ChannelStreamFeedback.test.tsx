/**
 * Arrival: with `reply_feedback` on, Decibyl's reply in the thread carries
 * "Was this useful?", and what the person already said is read back; with it
 * off, nothing is shown and nothing is asked.
 */
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const mine = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ reply_feedback: false }));
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: vi.fn().mockResolvedValue({ data: { text: '' } }),
    decideApiV1TimelineDecidePost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    translateTextApiV1TranslatePost: vi.fn(),
    threadChipsApiV1TimelineChipsGet: vi.fn().mockResolvedValue({ data: { chips: [] } }),
    postMessageApiV1TimelineMessagePost: vi.fn(),
    giveFeedbackApiV1FeedbackPost: vi.fn(),
    myFeedbackApiV1FeedbackMineGet: mine,
    clientEventApiV1EventsClientPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { ChannelStream } from '../ChannelStream';

const reply = {
    id: 21,
    at: '2026-10-07T06:00:00Z',
    kind: 'message',
    actor: 'agent',
    summary: 'Your appointment is on Thursday.',
    payload: { from: 'Decibyl', body: 'Your appointment is on Thursday.' },
    is_deliverable: false,
    workflow_id: null,
    workflow_run_id: null,
    folder_id: null,
};

beforeEach(() => {
    timeline.mockReset();
    timeline.mockResolvedValue({ data: { events: [reply], next_before_at: null, next_before_id: null } });
    mine.mockReset();
    mine.mockResolvedValue({ data: { answers: {}, reasons: [] } });
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
});

describe("Was this useful? under Decibyl's reply", () => {
    it('is shown when switched on, and asks what was already said', async () => {
        flags.reply_feedback = true;
        render(<ChannelStream assistant botNames={{}} />);
        expect(await screen.findByText('Was this useful?')).toBeTruthy();
        await waitFor(() => expect(mine).toHaveBeenCalled());
        expect(mine.mock.calls[0][0].query).toEqual({ subject_kind: 'reply', ids: [21] });
    });

    it('shows nothing and asks nothing while off', async () => {
        flags.reply_feedback = false;
        render(<ChannelStream assistant botNames={{}} />);
        expect(await screen.findByText('Your appointment is on Thursday.')).toBeTruthy();
        expect(screen.queryByText('Was this useful?')).toBeNull();
        expect(mine).not.toHaveBeenCalled();
    });
});
