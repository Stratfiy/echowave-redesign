/**
 * The card a bot's proposed change to itself appears on: the diff, and the
 * two things to press. Once pressed, the card says so.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const settle = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({ settleEditApiV1TimelineEditsSettlePost: settle }));

import type { TimelineEvent } from '@/client/types.gen';

import { diffLines, EditCard } from '../EditCard';

const DIFF = [
    '--- Find a slot (now)',
    '+++ Find a slot (proposed)',
    '@@ -1 +1 @@',
    '-Ask for a date.',
    "+Ask for the patient's name, then a date.",
    '',
].join('\n');

const event = (over: Partial<TimelineEvent> = {}): TimelineEvent =>
    ({
        id: 9,
        at: '2026-09-14T06:00:00Z',
        kind: 'edit_proposed',
        actor: 'agent',
        summary: 'Proposed a change to Find a slot',
        payload: { step: 'Find a slot', why: 'Name first', diff: DIFF },
        is_deliverable: false,
        workflow_id: 3,
        workflow_run_id: null,
        folder_id: null,
        ...over,
    }) as TimelineEvent;

beforeEach(() => settle.mockReset());

describe('reading the diff', () => {
    it('drops the file header and marks added and removed lines', () => {
        const lines = diffLines(DIFF);
        expect(lines.map((l) => l.kind)).toEqual(['meta', 'del', 'add']);
        expect(lines[1].text).toBe('Ask for a date.');
    });
});

describe('the card', () => {
    it('shows the step, the why and the diff, with Publish and Discard', () => {
        render(<EditCard event={event()} />);
        expect(screen.getByText('Change to Find a slot')).toBeTruthy();
        expect(screen.getByText('Name first')).toBeTruthy();
        expect(screen.getByLabelText('What changes').textContent).toContain("Ask for the patient's name");
        expect(screen.getByRole('button', { name: 'Publish' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Discard' })).toBeTruthy();
    });

    it('publishing settles the row and hands the updated row back', async () => {
        const updated = event({ payload: { step: 'Find a slot', diff: DIFF, decided: { action: 'publish', at: '2026-09-14T06:01:00Z' } } });
        settle.mockResolvedValue({ data: updated });
        const onSettled = vi.fn();
        render(<EditCard event={event()} onSettled={onSettled} />);
        fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
        await waitFor(() => expect(settle).toHaveBeenCalledWith({ body: { event_id: 9, action: 'publish' } }));
        await waitFor(() => expect(onSettled).toHaveBeenCalledWith(updated));
    });

    it('a settled card says what was done and offers nothing to press', () => {
        render(<EditCard event={event({ payload: { step: 'Rules', diff: DIFF, decided: { action: 'discard' } } })} />);
        expect(screen.getByText(/Discarded/)).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
    });

    it('a refused click is said on the card', async () => {
        settle.mockResolvedValue({ error: { detail: 'Already settled.' } });
        render(<EditCard event={event()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
        expect((await screen.findByRole('alert')).textContent).toContain('Already settled');
    });
});
