import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AGENT_TABS } from '@/app/workflow/[workflowId]/components/AgentTabs';

import { BotThread } from '../BotThread';

const timeline = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({ timelineApiV1TimelineGet: timeline }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

function event(over: Record<string, unknown> = {}) {
    return {
        id: 1,
        at: '2026-09-13T09:15:00Z',
        kind: 'outcome_filed',
        actor: 'agent',
        summary: 'Booked Tuesday 4pm for Ramesh',
        payload: {},
        is_deliverable: true,
        workflow_id: 8,
        workflow_run_id: null,
        folder_id: null,
        ...over,
    };
}

beforeEach(() => timeline.mockReset());

describe('the bot thread', () => {
    it('is reachable from the bot, which is the whole point', () => {
        // BotRibbon was built in this repo and mounted nowhere — a component
        // that renders correctly and that nobody can open is the same silent
        // absence as the table it was meant to read. This asserts the tab
        // exists and points somewhere, so that cannot happen quietly again.
        const thread = AGENT_TABS.find((tab) => tab.key === 'thread');
        expect(thread).toBeTruthy();
        expect(AGENT_TABS[0].key).toBe('thread');
    });

    it('renders the sentence the server wrote, not one assembled here', async () => {
        timeline.mockResolvedValue({
            data: { events: [event()], next_before_at: null, next_before_id: null },
        });
        render(<BotThread workflowId={8} />);
        expect(await screen.findByText('Booked Tuesday 4pm for Ramesh')).toBeTruthy();
    });

    it('asks only for this bot', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_id: null } });
        render(<BotThread workflowId={8} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(timeline.mock.calls[0][0].query.workflow_id).toBe(8);
    });

    it('says a quiet bot is quiet rather than showing nothing at all', async () => {
        timeline.mockResolvedValue({ data: { events: [], next_before_id: null } });
        render(<BotThread workflowId={8} />);
        expect(await screen.findByText('Nothing yet.')).toBeTruthy();
    });

    it('reports a refusal instead of looking empty', async () => {
        // An error rendered as an empty thread reads as "this bot has done
        // nothing", which is a different claim and the wrong one.
        timeline.mockResolvedValue({ error: { detail: 'Workflow not found' } });
        render(<BotThread workflowId={8} />);
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.queryByText('Nothing yet.')).toBeNull();
    });

    it('renders a kind it has never heard of', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [event({ kind: 'invented_next_year', summary: 'Something new' })],
                next_before_id: null,
            },
        });
        render(<BotThread workflowId={8} />);
        expect(await screen.findByText('Something new')).toBeTruthy();
    });

    it('pages with both halves of the cursor or not at all', async () => {
        timeline.mockResolvedValue({
            data: {
                events: [event()],
                next_before_at: '2026-09-13T09:00:00Z',
                next_before_id: 7,
            },
        });
        render(<BotThread workflowId={8} />);
        const more = await screen.findByRole('button', { name: 'Show earlier' });
        more.click();
        await waitFor(() => expect(timeline).toHaveBeenCalledTimes(2));
        const query = timeline.mock.calls[1][0].query;
        expect(query.before_id).toBe(7);
        expect(query.before_at).toBe('2026-09-13T09:00:00Z');
    });

    it('offers no way to page when half a cursor comes back', async () => {
        // Half a cursor narrows nothing server-side, so a button would fetch
        // page one again and look like a thread that repeats itself.
        timeline.mockResolvedValue({
            data: { events: [event()], next_before_at: null, next_before_id: 7 },
        });
        render(<BotThread workflowId={8} />);
        await screen.findByText('Booked Tuesday 4pm for Ramesh');
        expect(screen.queryByRole('button', { name: 'Show earlier' })).toBeNull();
    });
});

describe('what is new since last time', () => {
    it('draws the NEW line under the rows newer than the previous visit', async () => {
        localStorage.setItem('decibyl.bot-seen', JSON.stringify({ '7': '2026-09-13T06:00:00Z' }));
        timeline.mockResolvedValue({
            data: {
                events: [
                    event({ id: 3, at: '2026-09-13T07:00:00Z', summary: 'Newest' }),
                    event({ id: 2, at: '2026-09-13T06:30:00Z', summary: 'Also new' }),
                    event({ id: 1, at: '2026-09-13T05:00:00Z', summary: 'Already read' }),
                ],
                next_before_at: null,
                next_before_id: null,
            },
        });
        render(<BotThread workflowId={7} />);
        await waitFor(() => expect(screen.getByText('Newest')).toBeTruthy());
        const items = Array.from(document.querySelectorAll('ol > li'));
        const labels = items.map((li) => li.getAttribute('aria-label') ?? li.textContent);
        expect(labels.findIndex((l) => l === 'New')).toBe(2);
        // And the visit moves the mark, so a reload shows nothing as new.
        expect(JSON.parse(localStorage.getItem('decibyl.bot-seen')!)['7'] > '2026-09-13T06:00:00Z').toBe(true);
        localStorage.clear();
    });

    it('draws no line on a first visit', async () => {
        localStorage.clear();
        timeline.mockResolvedValue({
            data: { events: [event({ id: 1, at: '2026-09-13T07:00:00Z' })], next_before_at: null, next_before_id: null },
        });
        render(<BotThread workflowId={8} />);
        await waitFor(() => expect(timeline).toHaveBeenCalled());
        expect(screen.queryByLabelText('New')).toBeNull();
    });
});
