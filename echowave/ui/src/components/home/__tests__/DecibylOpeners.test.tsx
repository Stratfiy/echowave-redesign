/**
 * The two questions Decibyl answers on arrival, from the account's own
 * numbers, as replies under the question.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const home = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({ teamHomeApiV1TeamHomeGet: home }));

import type { Headline, TeamMember } from '@/client/types.gen';

import { DecibylOpeners, needsAttention, whatHappened } from '../DecibylOpeners';

const headline = (over: Partial<Headline> = {}): Headline => ({
    agents: 2, live: 2, calls: 8, answered: 6, outcomes: 3, needs_attention: 0, ...over,
});
const member = (over: Partial<TeamMember>): TeamMember =>
    ({
        workflow_id: 3, workflow_uuid: null, name: 'Front desk', is_live: true, status: 'ok', tone: 'ok',
        at: null, calls: 7, answered: 5, outcomes: 3, failures: 0, ...over,
    }) as TeamMember;

beforeEach(() => home.mockReset());

describe('the answers', () => {
    it('what happened is the numbers in sentences, busiest bots first', () => {
        const lines = whatHappened(headline(), [member({ name: 'Quiet', calls: 1 }), member({})], 168);
        expect(lines[0]).toBe('8 calls this week, 6 answered, 3 outcomes filed.');
        expect(lines[1]).toContain('Front desk: 7 calls');
        expect(lines[2]).toContain('Quiet: 1 call');
    });

    it('what needs attention names failures, silence, and the doors', () => {
        const answer = needsAttention(
            headline(),
            [member({ failures: 2 }), member({ name: 'Sales', outcomes: 0 })],
            [{ kind: 'connector', text: 'Fix the calendar', action: 'link', href: '/integrations/apps', prompt: null }],
        );
        expect(answer.lines).toEqual(['Front desk failed 2 times today.', 'Sales took 7 calls and filed nothing.']);
        expect(answer.links).toEqual([{ text: 'Fix the calendar', href: '/integrations/apps' }]);
    });

    it('nothing wrong is said plainly', () => {
        expect(needsAttention(headline(), [member({})], []).lines).toEqual(['Nothing needs you right now.']);
    });
});

describe('asking', () => {
    it('the week is fetched over seven days and answered under the question', async () => {
        home.mockResolvedValue({ data: { hours: 168, headline: headline({ calls: 40 }), suggestions: [], members: [member({})] } });
        render(<DecibylOpeners headline={headline()} members={[]} suggestions={[]} />);
        fireEvent.click(screen.getByRole('button', { name: /What happened this week/ }));
        await waitFor(() => expect(home).toHaveBeenCalledWith({ query: { hours: 168 } }));
        const answer = await screen.findByLabelText("Decibyl's answer");
        expect(answer.textContent).toContain('What happened this week?');
        expect(answer.textContent).toContain('40 calls this week');
    });

    it('attention is answered from what the page already has', () => {
        render(<DecibylOpeners headline={headline()} members={[member({ failures: 1 })]} suggestions={[]} />);
        fireEvent.click(screen.getByRole('button', { name: /What needs my attention/ }));
        expect(screen.getByLabelText("Decibyl's answer").textContent).toContain('Front desk failed 1 time today.');
        expect(home).not.toHaveBeenCalled();
    });
});
