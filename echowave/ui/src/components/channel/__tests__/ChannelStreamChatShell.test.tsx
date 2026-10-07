/**
 * The thread under `chat_shell` (screen 04), and the history-failure fix
 * that holds with the flag off too: a thread that did not load is a
 * failure with Retry, never "This channel is quiet".
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const timeline = vi.hoisted(() => vi.fn());
const draft = vi.hoisted(() => vi.fn());
const post = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    timelineApiV1TimelineGet: timeline,
    replyDraftTextApiV1TimelineDraftGet: draft,
    decideApiV1TimelineDecidePost: vi.fn(),
    settleActionApiV1TimelineActionsSettlePost: vi.fn(),
    settleEditApiV1TimelineEditsSettlePost: vi.fn(),
    translateTextApiV1TranslatePost: vi.fn(),
    threadChipsApiV1TimelineChipsGet: async () => ({ data: { chips: [] } }),
    postMessageApiV1TimelineMessagePost: post,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { ChannelStream } from '../ChannelStream';

let next = 100;
const row = (over: Record<string, unknown> = {}) => ({
    id: ++next,
    at: `2026-10-07T10:${String(next % 60).padStart(2, '0')}:00Z`,
    kind: 'message',
    actor: 'agent',
    summary: 'Here is your plan.',
    payload: { body: 'Here is your plan.' },
    is_deliverable: false,
    workflow_id: null,
    workflow_run_id: null,
    folder_id: null,
    ...over,
});
const page = (events: unknown[]) => ({ data: { events: [...events].reverse(), next_before_at: null, next_before_id: null } });

beforeEach(() => {
    timeline.mockReset();
    draft.mockReset();
    draft.mockResolvedValue({ data: { text: '' } });
    post.mockReset();
    post.mockResolvedValue({ data: { asked: [] } });
});

describe('a history that did not load', () => {
    it('shows the failure with Retry and reports it, never an empty thread', async () => {
        timeline.mockResolvedValueOnce({ error: { detail: 'boom' } });
        const onLoadState = vi.fn();
        render(<ChannelStream assistant botNames={{}} onLoadState={onLoadState} />);
        expect(await screen.findByText('Could not load this conversation')).toBeTruthy();
        expect(screen.queryByText('This channel is quiet.')).toBeNull();
        expect(onLoadState).toHaveBeenLastCalledWith('error');
        timeline.mockResolvedValue(page([row({ actor: 'human', summary: 'hi', payload: { body: 'hi' } })]));
        fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
        expect(await screen.findByText('hi')).toBeTruthy();
        expect(onLoadState).toHaveBeenLastCalledWith('ready');
    });
});

describe('screen 04 states', () => {
    it('puts the turn status under the request while the reply forms', async () => {
        const ask = row({ actor: 'human', summary: 'Plan my day', payload: { body: 'Plan my day' } });
        const reading = row({ kind: 'activity', summary: 'Read the team (2 agents)', payload: {} });
        timeline.mockResolvedValue(page([ask, reading]));
        const onWaitingChange = vi.fn();
        const onTurnStatus = vi.fn();
        render(
            <ChannelStream
                assistant
                chatShell
                botNames={{}}
                waitingFor={{ since: new Date().toISOString(), bots: [0] }}
                onWaitingChange={onWaitingChange}
                onTurnStatus={onTurnStatus}
            />,
        );
        const status = await screen.findByTestId('turn-status');
        expect(status.textContent).toContain('Working');
        expect(onWaitingChange).toHaveBeenLastCalledWith(true);
        expect(onTurnStatus).toHaveBeenLastCalledWith(expect.objectContaining({ state: 'running', requestId: ask.id }));
    });

    it('marks a stopped reply partial, keeps its text, and retries the request', async () => {
        const ask = row({ actor: 'human', summary: 'Tell me everything', payload: { body: 'Tell me everything' } });
        const reply = row({ summary: 'The first half', payload: { body: 'The first half', stopped: true } });
        timeline.mockResolvedValue(page([ask, reply]));
        render(<ChannelStream assistant chatShell threadId="t-1" botNames={{}} />);
        expect(await screen.findByText('The first half')).toBeTruthy();
        expect(screen.getByText('Stopped · partial answer')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body).toEqual({ assistant: true, thread_id: 't-1', text: 'Tell me everything' });
    });

    it('never dresses a failed reply as a finished one', async () => {
        timeline.mockResolvedValue(
            page([row({ actor: 'human', payload: { body: 'x' } }), row({ summary: 'I could not think that through', payload: { body: 'I could not think that through', failed: true } })]),
        );
        render(<ChannelStream assistant chatShell botNames={{}} />);
        expect(await screen.findByText('Failed')).toBeTruthy();
        expect(screen.queryByText('Done')).toBeNull();
    });

    it('opens the sources a reply was read from, on demand', async () => {
        const sources = [
            { kind: 'team', label: 'Your team', status: 'read' },
            { kind: 'knowledge', label: 'Company knowledge', status: 'unavailable' },
        ];
        const reply = row({ summary: 'Two agents are live.', payload: { body: 'Two agents are live.' } });
        timeline.mockResolvedValue(page([row({ actor: 'human', payload: { body: 'q' } }), row({ kind: 'activity', payload: { sources } }), reply]));
        const onOpenSources = vi.fn();
        render(<ChannelStream assistant chatShell botNames={{}} onOpenSources={onOpenSources} />);
        fireEvent.click(await screen.findByTestId('open-sources'));
        expect(onOpenSources).toHaveBeenCalledWith(sources, reply.id);
    });

    it('offers New content instead of moving a reader who scrolled up', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const first = row({ actor: 'human', payload: { body: 'one' }, summary: 'one' });
        timeline.mockResolvedValue(page([first]));
        render(<ChannelStream assistant chatShell botNames={{}} />);
        await screen.findByText('one');
        const scroller = screen.getByText('one').closest('.overflow-y-auto') as HTMLElement;
        Object.defineProperty(scroller, 'scrollHeight', { value: 2000, configurable: true });
        Object.defineProperty(scroller, 'clientHeight', { value: 500, configurable: true });
        scroller.scrollTop = 100;
        fireEvent.scroll(scroller);
        timeline.mockResolvedValue(page([first, row({ summary: 'two', payload: { body: 'two' } })]));
        await act(async () => {
            vi.advanceTimersByTime(5100);
        });
        const button = await screen.findByTestId('new-content');
        expect(scroller.scrollTop).toBe(100);
        scroller.scrollTo = vi.fn();
        fireEvent.click(button);
        expect(scroller.scrollTo).toHaveBeenCalled();
        expect(screen.queryByTestId('new-content')).toBeNull();
        vi.useRealTimers();
    });

    it('shows none of it with the flag off', async () => {
        timeline.mockResolvedValue(page([row({ actor: 'human', payload: { body: 'x' } }), row({ payload: { body: 'y', stopped: true }, summary: 'y' })]));
        render(<ChannelStream assistant botNames={{}} />);
        await screen.findByText('y');
        expect(screen.queryByText('Stopped · partial answer')).toBeNull();
        expect(screen.queryByTestId('turn-status')).toBeNull();
    });
});
