/**
 * Arrival (screen 28): with `support_help` on, a failed reply from Decibyl
 * (in the `chat_shell` thread, beside Retry)
 * offers "Get help" about that one reply; off, or on a reply that did not
 * fail, nothing is offered.
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ support_help: false }));
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
    myFeedbackApiV1FeedbackMineGet: vi.fn().mockResolvedValue({ data: { answers: {}, reasons: [] } }),
    clientEventApiV1EventsClientPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { ChannelStream } from '../ChannelStream';

function reply(id: number, payload: Record<string, unknown>) {
    return {
        id,
        at: '2026-10-07T06:00:00Z',
        kind: 'message',
        actor: 'agent',
        summary: String(payload.body),
        payload: { from: 'Decibyl', ...payload },
        is_deliverable: false,
        workflow_id: null,
        workflow_run_id: null,
        folder_id: null,
    };
}

beforeEach(() => {
    timeline.mockReset();
    timeline.mockResolvedValue({
        data: {
            events: [reply(31, { body: 'That did not work.', failed: true }), reply(32, { body: 'Here you go.' })],
            next_before_at: null,
            next_before_id: null,
        },
    });
    Element.prototype.scrollIntoView = vi.fn();
});

describe('Get help under a failed reply', () => {
    it('links to a request about that reply when Help is on', async () => {
        flags.support_help = true;
        render(<ChannelStream assistant chatShell botNames={{}} />);
        const link = await screen.findByTestId('reply-get-help');
        expect(link.getAttribute('href')).toBe('/help/new?reply=31');
        expect(screen.getAllByTestId('reply-get-help')).toHaveLength(1);
    });

    it('is not offered while Help is off', async () => {
        flags.support_help = false;
        render(<ChannelStream assistant chatShell botNames={{}} />);
        expect(await screen.findByText('That did not work.')).toBeTruthy();
        expect(screen.queryByTestId('reply-get-help')).toBeNull();
    });
});
