/**
 * The stream, pointed at a channel or at one bot.
 *
 * Guarded: which rows it asks for, that a finished call is a row with a door
 * to the call, and that on a bot's own chat the rows newer than the previous
 * visit sit under a NEW line.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const translate = vi.hoisted(() => vi.fn());
const draft = vi.hoisted(() => vi.fn());
const chips = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: draft,
    decideApiV1TimelineDecidePost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    translateTextApiV1TranslatePost: translate,
    threadChipsApiV1TimelineChipsGet: chips,
    postMessageApiV1TimelineMessagePost: post,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/components/browser/BrowserPanel', () => ({
    BrowserPanel: ({ sessionUuid }: { sessionUuid: string }) => <div data-testid="browser-panel">{sessionUuid}</div>,
}));

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
    draft.mockReset();
    draft.mockResolvedValue({ data: { text: '' } });
    chips.mockReset();
    chips.mockResolvedValue({ data: { chips: [] } });
    post.mockReset();
    post.mockResolvedValue({ data: { asked: [] } });
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

    it("an agent's own chat by workflow", async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.workflow_id).toBe(3);
        expect(await screen.findByText('Nothing yet.')).toBeTruthy();
    });
});

describe("which of Decibyl's chats", () => {
    it('no thread named reads the original conversation', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        render(<ChannelStream assistant botNames={{}} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.assistant).toBe(true);
        expect(timeline.mock.calls[0][0].query.thread_id).toBeUndefined();
    });

    it('a named thread is asked for by name, and written to by name', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        chips.mockResolvedValue({ data: { chips: [{ text: 'What ran today?', kind: 'time' }] } });
        render(<ChannelStream assistant threadId="t-2" botNames={{}} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.thread_id).toBe('t-2');
        // A chip pressed in this chat is a line in this chat, or the reply
        // lands in the original one and this screen never shows it.
        fireEvent.click(await screen.findByRole('button', { name: 'What ran today?' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.thread_id).toBe('t-2');
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
        // Oldest first on screen: the day, the read row, NEW, then the two new rows.
        expect(rows.findIndex((r) => r === 'New')).toBe(2);
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

describe('an agent that was asked shows as thinking', () => {
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

    it('says so when the wait runs out, rather than vanishing', async () => {
        // A spinner that never stops is a lie, but a row that disappears is
        // worse: the bot was working, the line went, and nothing said whether
        // it finished, failed, or is still going. Silence is a state.
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        const since = new Date(Date.now() - 4 * 60 * 1000).toISOString();
        render(
            <ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} waitingFor={{ since, bots: [3] }} />,
        );
        expect(await screen.findByLabelText('Front desk has not replied')).toBeTruthy();
        expect(screen.queryByLabelText('Front desk is thinking')).toBeNull();
        expect(screen.getByText(/No reply yet/)).toBeTruthy();
    }, 10000);
});

describe('a proposed change to the agent', () => {
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
    it('is a card with Confirm on the thread, attributed to the agent', async () => {
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

describe('what the agent read on the way', () => {
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

describe('the reply forming', () => {
    it('shows in the thinking row as it grows, in place of the spinner', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_at: null, next_before_id: null } });
        draft.mockResolvedValue({ data: { text: 'Front desk took 8 calls' } });
        const since = new Date(Date.now() - 1000).toISOString();
        render(<ChannelStream assistant assistantName="Decibyl" botNames={{}} waitingFor={{ since, bots: [0] }} />);
        expect(await screen.findByLabelText('Decibyl is thinking')).toBeTruthy();
        expect(await screen.findByTestId('forming')).toBeTruthy();
        expect(screen.getByTestId('forming').textContent).toContain('Front desk took 8 calls');
        expect(screen.queryByText('Thinking…')).toBeNull();
        // Decibyl's thread asks for the assistant's draft, no bot.
        expect(draft.mock.calls[0][0].query).toBeUndefined();
    });
});


describe('the thread carries its own next steps', () => {
    const reply = () =>
        timeline.mockResolvedValue({
            data: { events: [event({ workflow_id: null })], next_before_at: null, next_before_id: null },
        });

    it('offers the account\'s own questions under the reply', async () => {
        reply();
        chips.mockResolvedValue({
            data: { chips: [{ kind: 'asked_before', text: 'check my email' }] },
        });
        render(<ChannelStream assistant botNames={{}} />);
        expect(await screen.findByRole('button', { name: 'check my email' })).toBeTruthy();
    });

    it('sends the chip as an ordinary message', async () => {
        reply();
        chips.mockResolvedValue({
            data: { chips: [{ kind: 'asked_before', text: 'check my email' }] },
        });
        render(<ChannelStream assistant botNames={{}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'check my email' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        // A chip a person could not also have typed teaches them nothing.
        expect(post.mock.calls[0][0].body).toEqual({ assistant: true, thread_id: null, text: 'check my email' });
    });

    it('asks for the chips of this thread, and a follow-up goes back to its helper', async () => {
        reply();
        chips.mockResolvedValue({
            data: { chips: [{ kind: 'follow_up', text: 'Go deeper on point 2', helper: 'research' }] },
        });
        render(<ChannelStream assistant threadId="t-9" botNames={{}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Go deeper on point 2' }));
        expect(chips.mock.calls[0][0].query.thread_id).toBe('t-9');
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body).toEqual({
            assistant: true,
            thread_id: 't-9',
            text: 'Go deeper on point 2',
            helper: 'research',
        });
    });

    it('clears them once one is pressed', async () => {
        // Leaving them under the question they just asked reads as if
        // nothing happened.
        reply();
        chips.mockResolvedValue({
            data: { chips: [{ kind: 'asked_before', text: 'check my email' }] },
        });
        render(<ChannelStream assistant botNames={{}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'check my email' }));
        await waitFor(() =>
            expect(screen.queryByRole('button', { name: 'check my email' })).toBeNull(),
        );
    });

    it('on an agent\'s own chat, only its reply\'s next steps, sent to the agent', async () => {
        // The workspace's questions are Decibyl's; the agent's chat gets the
        // next steps of the agent's own reply (found on staging: a research
        // agent's report had nothing to tap).
        reply();
        chips.mockResolvedValue({
            data: { chips: [{ kind: 'follow_up', text: 'Go deeper on point 3', helper: null }] },
        });
        render(<ChannelStream workflowId={3} botNames={{}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Go deeper on point 3' }));
        expect(chips.mock.calls[0][0].query).toEqual({ workflow_id: 3 });
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body).toEqual({ workflow_id: 3, text: 'Go deeper on point 3' });
    });

    it('a thread whose chips fail to load is still a thread', async () => {
        reply();
        chips.mockResolvedValue({ error: { detail: 'nope' } });
        render(<ChannelStream assistant botNames={{}} />);
        expect(await screen.findByText('Booked Meera for 4pm.')).toBeTruthy();
        expect(screen.queryByTestId('thread-chips')).toBeNull();
    });
});

describe('the little markdown a model writes', () => {
    /** The defect: a reply printed `**Wednesday Web Drop**` with the asterisks
     *  on screen. Nothing rendered markdown and no library is installed. */
    const withBody = (body: string) =>
        timeline.mockResolvedValue({
            data: {
                events: [event({ payload: { body } })],
                next_before_at: null,
                next_before_id: null,
            },
        });

    it('renders bold as bold, without the asterisks', async () => {
        withBody('1. **Wednesday Web Drop** — Mobbin');
        render(<ChannelStream folderId={5} botNames={{}} />);
        const strong = await screen.findByText('Wednesday Web Drop');
        expect(strong.tagName).toBe('STRONG');
        expect(screen.queryByText(/\*\*/)).toBeNull();
    });

    it('keeps the words around it', async () => {
        withBody('1. **Web Drop** — Mobbin');
        render(<ChannelStream folderId={5} botNames={{}} />);
        await screen.findByText('Web Drop');
        expect(screen.getByText(/— Mobbin/)).toBeTruthy();
    });

    it('renders inline code', async () => {
        withBody('the tool is `GMAIL_SEND_EMAIL`');
        render(<ChannelStream folderId={5} botNames={{}} />);
        const code = await screen.findByText('GMAIL_SEND_EMAIL');
        expect(code.tagName).toBe('CODE');
    });

    it('leaves a handle its own colour, not emphasis', async () => {
        // Emphasis reads inside a tag token's neighbours, never across them.
        withBody('ask @frontdesk about **the booking**');
        render(<ChannelStream folderId={5} botNames={{}} />);
        const handle = await screen.findByText('@frontdesk');
        expect(handle.tagName).toBe('SPAN');
        expect((await screen.findByText('the booking')).tagName).toBe('STRONG');
    });

    it('never turns a relayed subject line into markup', async () => {
        // These replies carry text written by strangers -- one subject line in
        // the real case was flagged by the bot itself as probable phishing.
        withBody('subject: <img src=x onerror=alert(1)>');
        const { container } = render(<ChannelStream folderId={5} botNames={{}} />);
        await screen.findByText(/subject:/);
        expect(container.querySelector('img')).toBeNull();
    });

    it('leaves arithmetic alone', async () => {
        withBody('2 * 3 = 6');
        render(<ChannelStream folderId={5} botNames={{}} />);
        expect(await screen.findByText(/2 \* 3 = 6/)).toBeTruthy();
    });
});

describe('the rhythm of a thread', () => {
    it('says a name and a date once for a run by the same author', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 2, at: '2026-09-13T06:01:00Z', summary: 'Second line' }),
                    event({ id: 1, at: '2026-09-13T06:00:00Z', summary: 'First line' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findByText('Second line');
        expect(screen.getAllByText('Front desk').length).toBe(1);
    });

    it('says it again when somebody else speaks in between', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 3, at: '2026-09-13T06:02:00Z', summary: 'And again' }),
                    event({ id: 2, at: '2026-09-13T06:01:00Z', actor: 'human', summary: 'Thanks' }),
                    event({ id: 1, at: '2026-09-13T06:00:00Z', summary: 'First line' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findByText('And again');
        expect(screen.getAllByText('Front desk').length).toBe(2);
    });

    it('says it again after a long pause', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 2, at: '2026-09-13T09:00:00Z', summary: 'Hours later' }),
                    event({ id: 1, at: '2026-09-13T06:00:00Z', summary: 'First line' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findByText('Hours later');
        expect(screen.getAllByText('Front desk').length).toBe(2);
    });

    it('puts a date between two days', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 2, at: '2026-09-14T06:00:00Z', summary: 'The next day' }),
                    event({ id: 1, at: '2026-09-13T06:00:00Z', summary: 'The day before' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream workflowId={3} botNames={{ 3: 'Front desk' }} />);
        await screen.findByText('The next day');
        const days = Array.from(document.querySelectorAll('ol > li')).filter(
            (li) => li.getAttribute('aria-label') && li.getAttribute('aria-label') !== 'New',
        );
        expect(days.length).toBe(2);
        // And the two dates differ, so the divider is the day and not a
        // line drawn above every row.
        expect(new Set(days.map((li) => li.getAttribute('aria-label'))).size).toBe(2);
    });
});

describe("Decibyl's private browser", () => {
    it('shows the panel for the session the row names', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        kind: 'browser_session',
                        summary: 'Browsing: check my bill',
                        payload: { from: 'Decibyl', session_uuid: 'abc-123', by: 1 },
                        workflow_id: null,
                        folder_id: null,
                    }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream assistant botNames={{}} />);
        expect((await screen.findByTestId('browser-panel')).textContent).toBe('abc-123');
    });
});

describe('a course started from the conversation', () => {
    const course = (goalId: string | null) =>
        event({
            kind: 'activity',
            summary: 'Course: Python basics',
            workflow_id: null,
            folder_id: null,
            payload: {
                learning_course: {
                    goal_id: goalId,
                    title: 'Python basics',
                    href: goalId ? `/overview?learn=${goalId}` : '/overview?learn=new&topic=Python+basics',
                },
            },
        });

    it('opens the lesson right here in Chat, not on another screen', async () => {
        timeline.mockResolvedValue({ data: { events: [course('g-1')], next_before_at: null, next_before_id: null } });
        const onOpenLesson = vi.fn();
        render(<ChannelStream assistant botNames={{}} onOpenLesson={onOpenLesson} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Open the lesson' }));
        expect(onOpenLesson).toHaveBeenCalledWith('g-1', 'Python basics');
    });

    it('before it can start, the same button opens the start with the course named', async () => {
        timeline.mockResolvedValue({ data: { events: [course(null)], next_before_at: null, next_before_id: null } });
        const onOpenLesson = vi.fn();
        render(<ChannelStream assistant botNames={{}} onOpenLesson={onOpenLesson} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Open the lesson' }));
        expect(onOpenLesson).toHaveBeenCalledWith(null, 'Python basics');
    });

    it('without a lesson pane it is a link to the lesson', async () => {
        timeline.mockResolvedValue({ data: { events: [course('g-1')], next_before_at: null, next_before_id: null } });
        render(<ChannelStream assistant botNames={{}} />);
        const link = await screen.findByRole('link', { name: 'Open the lesson' });
        expect(link.getAttribute('href')).toBe('/overview?learn=g-1');
    });
});

describe('a row with something to open is never folded away', () => {
    it('keeps a course card and a saved report out of a run of steps', async () => {
        const { groupRows } = await import('../ChannelStream');
        const read = event({ id: 1, kind: 'activity', summary: 'Read the team (0 agents)', workflow_id: null, folder_id: null });
        const course = event({
            id: 2,
            kind: 'activity',
            summary: 'Course: Python',
            workflow_id: null,
            folder_id: null,
            payload: { learning_course: { goal_id: 'g-1', title: 'Python', href: '/overview?learn=g-1' } },
        });
        const report = event({
            id: 3,
            kind: 'activity',
            summary: 'Saved report: GST',
            workflow_id: null,
            folder_id: null,
            payload: { saved_report: { uuid: 'r-1', title: 'GST' } },
        });
        const groups = groupRows([read, course, report] as never);
        expect(groups.map((g) => g.events.map((e) => e.id))).toEqual([[1], [2], [3]]);
    });

    it('a course after a reading still shows its button', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({
                        id: 2,
                        kind: 'activity',
                        at: '2026-09-13T06:00:02Z',
                        summary: 'Course: Python',
                        workflow_id: null,
                        folder_id: null,
                        payload: { learning_course: { goal_id: 'g-1', title: 'Python', href: '/overview?learn=g-1' } },
                    }),
                    event({ id: 1, kind: 'activity', summary: 'Read the team (0 agents)', workflow_id: null, folder_id: null }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<ChannelStream assistant botNames={{}} onOpenLesson={vi.fn()} />);
        expect(await screen.findByRole('button', { name: 'Open the lesson' })).toBeTruthy();
    });
});

describe('the next steps are in view', () => {
    it('chips that arrive after the reply keep the thread at the bottom', async () => {
        // At 390px the chips landed below the fold: the thread had been
        // scrolled to the bottom before they arrived, and nobody saw them.
        const height = vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(1000);
        let release: (v: unknown) => void = () => {};
        chips.mockReturnValue(new Promise((resolve) => (release = resolve)));
        timeline.mockResolvedValue({
            data: { events: [event({ workflow_id: null, folder_id: null })], next_before_at: null, next_before_id: null },
        });
        const { container } = render(<ChannelStream assistant botNames={{}} />);
        await screen.findByText('Booked Meera for 4pm.');
        const scroller = container.firstElementChild as HTMLElement;
        scroller.scrollTop = 0;
        height.mockReturnValue(1400);
        release({ data: { chips: [{ kind: 'follow_up', text: 'Quiz me on this', helper: 'learning_guide' }] } });
        await screen.findByRole('button', { name: 'Quiz me on this' });
        await waitFor(() => expect(scroller.scrollTop).toBe(1400));
        height.mockRestore();
    });
});
