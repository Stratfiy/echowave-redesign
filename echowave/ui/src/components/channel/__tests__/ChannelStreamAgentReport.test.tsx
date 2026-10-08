/**
 * An agent's research, kept as a report from its own chat.
 *
 * Found on staging: a research agent built from Chat wrote its report in its
 * own chat and nothing could keep it -- Save as report sat under Decibyl's
 * replies only.
 */
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ research_reports: true }) as Record<string, boolean>);

vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: vi.fn().mockResolvedValue({ data: { text: '' } }),
    threadChipsApiV1TimelineChipsGet: vi.fn().mockResolvedValue({ data: { chips: [] } }),
    saveReplyAsReportApiV1HelpersReportsFromReplyPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({ useFeature: (name: string) => Boolean(flags[name]) }));

import { ChannelStream } from '../ChannelStream';

const reply = (over: Record<string, unknown> = {}) => ({
    id: 8,
    at: '2026-10-07T20:30:00Z',
    kind: 'message',
    actor: 'agent',
    summary: 'Up to Rs 78,000 for 3 kW.',
    payload: { body: 'Up to Rs 78,000 for 3 kW (https://pmsuryaghar.gov.in/).' },
    is_deliverable: false,
    workflow_id: 7,
    workflow_run_id: 3,
    folder_id: null,
    ...over,
});

beforeEach(() => {
    timeline.mockReset();
    flags.research_reports = true;
    Element.prototype.scrollIntoView = vi.fn();
});

describe("an agent's own chat", () => {
    it('offers Save as report under its reply', async () => {
        timeline.mockResolvedValue({ data: { events: [reply()], next_before_at: null, next_before_id: null } });
        render(<ChannelStream workflowId={7} botNames={{ 7: 'Research agent' }} />);
        expect(await screen.findByRole('button', { name: /Save as report/ })).toBeTruthy();
    });

    it('not in a channel, and not with reports off', async () => {
        timeline.mockResolvedValue({ data: { events: [reply({ folder_id: 5 })], next_before_at: null, next_before_id: null } });
        render(<ChannelStream folderId={5} botNames={{ 7: 'Research agent' }} />);
        await screen.findByText(/Rs 78,000/);
        expect(screen.queryByRole('button', { name: /Save as report/ })).toBeNull();
    });

    it('a failed reply has nothing to keep', async () => {
        flags.research_reports = true;
        timeline.mockResolvedValue({
            data: { events: [reply({ payload: { body: 'x', failed: true } })], next_before_at: null, next_before_id: null },
        });
        render(<ChannelStream workflowId={7} botNames={{ 7: 'Research agent' }} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(screen.queryByRole('button', { name: /Save as report/ })).toBeNull();
    });
});
