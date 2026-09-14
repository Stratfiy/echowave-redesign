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
const translate = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    decideApiV1TimelineDecidePost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    translateTextApiV1TranslatePost: translate,
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

describe('runs of the same thing fold into one line', () => {
    it('nine missed calls are one row with a count, and open on request', async () => {
        const missed = (id: number, hour: number) =>
            event({
                id,
                kind: 'call_ended',
                summary: 'Call not answered',
                at: `2026-09-13T${String(hour).padStart(2, '0')}:00:00Z`,
                payload: { run_id: id, answered: false },
            });
        timeline.mockResolvedValue({
            data: {
                events: [missed(3, 8), missed(2, 7), missed(1, 6)],
                next_before_at: null,
                next_before_id: null,
            },
        });
        const { getByText, queryAllByText } = render(
            <ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />,
        );
        await screen.findByText('3 calls not answered');
        expect(queryAllByText('Call not answered')).toHaveLength(0);
        getByText('Show all').click();
        await waitFor(() => expect(screen.getAllByText('Call not answered')).toHaveLength(3));
    });

    it('an answered call is never folded', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 2, kind: 'call_ended', summary: 'Call · 1m35s', payload: { run_id: 2, answered: true } }),
                    event({ id: 1, kind: 'call_ended', summary: 'Call · 0m40s', payload: { run_id: 1, answered: true } }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        expect(await screen.findByText('Call · 1m35s')).toBeTruthy();
        expect(screen.getByText('Call · 0m40s')).toBeTruthy();
    });
});

describe('a bot that was asked shows as thinking', () => {
    it('until a row of its own arrives', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        const since = new Date(Date.now() - 1000).toISOString();
        const { rerender } = render(
            <ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} waitingFor={{ since, bots: [3] }} />,
        );
        expect(await screen.findByLabelText('Front desk is thinking')).toBeTruthy();
        timeline.mockResolvedValue({
            data: {
                events: [event({ id: 5, at: new Date().toISOString(), summary: 'Booked.' })],
                next_before_at: null,
                next_before_id: null,
            },
        });
        rerender(
            <ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} waitingFor={{ since, bots: [3] }} />,
        );
        // The next poll brings the reply; the row goes.
        await waitFor(() => expect(screen.queryByLabelText('Front desk is thinking')).toBeNull(), { timeout: 7000 });
    }, 10000);
});

describe('a proposed change to the bot', () => {
    it('is a card with the diff on the thread', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        id: 9,
                        kind: 'edit_proposed',
                        summary: 'Proposed a change to Rules',
                        payload: { step: 'Rules', why: 'Never quote a price', diff: '@@ -1 +1 @@\n-Be polite.\n+Be polite. Never quote a price.\n' },
                    }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        expect(await screen.findByText('Change to Rules')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Publish' })).toBeTruthy();
    });
});


describe('a message in an Indian script can be translated', () => {
    it('offers Translate, and shows the translation under the message', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [event({ id: 4, actor: 'human', summary: 'வணக்கம், appointment please', payload: { body: 'வணக்கம், appointment please' } })],
                next_before_at: null,
                next_before_id: null,
            },
        });
        translate.mockResolvedValue({ data: { text: 'Hello, appointment please', source_language_code: 'ta-IN' } });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        (await screen.findByRole('button', { name: 'Translate' })).click();
        await waitFor(() => expect(translate).toHaveBeenCalledWith({ body: { text: 'வணக்கம், appointment please' } }));
        expect((await screen.findByLabelText('Translation')).textContent).toBe('Hello, appointment please');
    });

    it('offers nothing on an English message', async () => {
        timeline.mockResolvedValue({
            data: { events: [event({ id: 5, summary: 'Booked Meera for 4pm.' })], next_before_at: null, next_before_id: null },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findByText('Booked Meera for 4pm.');
        expect(screen.queryByRole('button', { name: 'Translate' })).toBeNull();
    });
});

describe('a proposed action', () => {
    it('is a card with Confirm on the thread, attributed to the bot', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        id: 9,
                        kind: 'action_proposed',
                        summary: 'Turn Front desk on',
                        payload: { label: 'Turn Front desk on', why: 'Off since Monday', reversible: true, state: 'proposed' },
                    }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        expect(await screen.findByRole('group', { name: 'Turn Front desk on' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy();
        expect(screen.getByText('Front desk')).toBeTruthy();
    });
});

describe('what the bot read on the way', () => {
    it('is a muted one-liner, folded when several run together, and does not end thinking', async () => {
        const since = new Date(Date.now() - 5_000).toISOString();
        const reading = (id: number, summary: string, secondsAfter: number) =>
            event({ id, kind: 'activity', summary, at: new Date(Date.now() - 5_000 + secondsAfter * 1000).toISOString() });
        timeline.mockResolvedValue({
            data: {
                events: [reading(3, 'Asked Front desk', 2), reading(2, 'Read 3 passages from price list', 1)],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(
            <ChannelStream
                workflowId={3}
                botNames={{ 3: 'Front desk' }}
                waitingFor={{ since, bots: [3] }}
            />,
        );
        expect(await screen.findByText(/Asked Front desk/)).toBeTruthy();
        expect(screen.getByText(/2 steps/)).toBeTruthy();
        expect(screen.queryByText(/Read 3 passages/)).toBeNull();
        // Readings are work, not the reply: the bot is still thinking.
        expect(screen.getByLabelText('Front desk is thinking')).toBeTruthy();
        screen.getByText('Show all').click();
        expect(await screen.findByText(/Read 3 passages from price list/)).toBeTruthy();
    });
});
