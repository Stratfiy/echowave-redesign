/**
 * The stream, pointed at a channel or at one bot.
 *
 * Guarded: which rows it asks for, that a finished call is a row with a door
 * to the call, and that on a bot's own chat the rows newer than the previous
 * visit sit under a NEW line.
 */
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    decideApiV1TimelineDecidePost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { ChannelStream } from '../ChannelStream';

const event = (over: Record<string, unknown> = {}) => ({
    id: 1,
    at: '2026-09-13T06:00:00Z',
    kind: 'message',
    actor: 'agent',
    summary: 'Booked Meera for 4pm.',
    payload: {},
    is_deliverable: false,
    workflow_id: 3,
    workflow_run_id: null,
    folder_id: 5,
    ...over,
});

beforeEach(() => {
    timeline.mockReset();
    localStorage.clear();
    Element.prototype.scrollIntoView = vi.fn();
});

describe('what it asks for', () => {
    it('a channel by folder', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        render(<ChannelStream folderId={5} botNames={{}} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.folder_id).toBe(5);
        expect(timeline.mock.calls[0][0].query.workflow_id).toBeUndefined();
    });

    it("a bot's own chat by workflow", async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.workflow_id).toBe(3);
        expect(await screen.findByText('Nothing yet.')).toBeTruthy();
    });
});

describe('a call is a row with a door', () => {
    it('links the call line to the run', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 9, kind: 'call_ended', summary: 'Call · 3m12s · booked', payload: { run_id: 77 } }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        const link = await screen.findByText('Call · 3m12s · booked');
        expect(link.closest('a')?.getAttribute('href')).toBe('/workflow/3/run/77');
        expect(screen.getByText('Front desk')).toBeTruthy();
    });
});

describe('what is new since last time', () => {
    it('draws the NEW line above the first row newer than the previous visit', async () => {
        localStorage.setItem('decibyl.bot-seen', JSON.stringify({ '3': '2026-09-13T05:30:00Z' }));
        timeline.mockResolvedValue({
            data: {
                // Newest first, as the API returns them.
                events: [
                    event({ id: 3, at: '2026-09-13T07:00:00Z', summary: 'Newest' }),
                    event({ id: 2, at: '2026-09-13T06:00:00Z', summary: 'Also new' }),
                    event({ id: 1, at: '2026-09-13T05:00:00Z', summary: 'Already read' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{}} />);
        await screen.findByText('Newest');
        const rows = Array.from(document.querySelectorAll('ol > li')).map(
            (li) => li.getAttribute('aria-label') ?? li.textContent,
        );
        // Oldest first on screen: read, NEW, then the two new rows.
        expect(rows.findIndex((r) => r === 'New')).toBe(1);
        // And the visit moved the mark.
        expect(JSON.parse(localStorage.getItem('decibyl.bot-seen')!)['3'] > '2026-09-13T05:30:00Z').toBe(true);
    });

    it('draws nothing in a channel', async () => {
        timeline.mockResolvedValue({
            data: { events: [event({ at: '2026-09-13T07:00:00Z' })], next_before_at: null, next_before_id: null },
        });
        render(<ChannelStream folderId={5} botNames={{}} />);
        await screen.findByText('Booked Meera for 4pm.');
        expect(screen.queryByLabelText('New')).toBeNull();
    });
});

describe('a file is a message', () => {
    it('shows the files a message carried as chips', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        id: 12,
                        actor: 'human',
                        summary: 'Shared rates.pdf',
                        payload: {
                            body: '',
                            attachments: [{ document_uuid: 'd1', filename: 'rates.pdf', size_bytes: 2048 }],
                        },
                    }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        const files = await screen.findByRole('list', { name: 'Files' });
        expect(files.textContent).toContain('rates.pdf');
        expect(files.textContent).toContain('2 KB');
    });
});
