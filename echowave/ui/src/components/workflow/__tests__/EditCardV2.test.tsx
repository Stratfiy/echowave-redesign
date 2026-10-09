/**
 * Agent editing follow-ups (editing_v2) on the card and the stack: hours and
 * files shown as before and after, Undo on a published card, "Waiting for
 * ..." for someone who may propose but not publish, and Publish all acting
 * card by card with a result for each.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const settle = vi.hoisted(() => vi.fn());
const publisher = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ editing_v2: true }));
vi.mock('@/client/sdk.gen', () => ({
    settleEditApiV1TimelineEditsSettlePost: settle,
    editPublisherApiV1TimelineEditsPublisherGet: publisher,
}));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import type { TimelineEvent } from '@/client/types.gen';

import { EditCard, forgetPublishers } from '../EditCard';
import { EditStack } from '../EditStack';

const event = (id: number, payload: Record<string, unknown>): TimelineEvent =>
    ({
        id,
        at: '2026-10-09T06:00:00Z',
        kind: 'edit_proposed',
        actor: 'agent',
        summary: 'Proposed a change',
        payload,
        is_deliverable: false,
        workflow_id: 3,
        workflow_run_id: null,
        folder_id: null,
    }) as TimelineEvent;

beforeEach(() => {
    settle.mockReset();
    publisher.mockReset();
    publisher.mockResolvedValue({ data: { can_publish: true } });
    forgetPublishers();
    flags.editing_v2 = true;
});

describe('the card, with editing_v2 on', () => {
    it('shows opening hours before and after', () => {
        render(
            <EditCard
                event={event(1, {
                    step: 'Opening hours',
                    what: 'hours',
                    hours: {
                        before: ['No opening hours of its own'],
                        after: ['Monday to Saturday, 9:30 am to 1 pm.', 'Closed Sunday 8 November 2026 (Diwali)'],
                    },
                })}
            />,
        );
        const hours = screen.getByTestId('hours-change').textContent ?? '';
        expect(hours).toContain('No opening hours of its own');
        expect(hours).toContain('Closed Sunday 8 November 2026 (Diwali)');
    });

    it('shows files the agent starts and stops reading', () => {
        render(
            <EditCard
                event={event(2, {
                    step: 'Files',
                    files: { added: [{ uuid: 'a', name: 'price-list.pdf' }], removed: [{ uuid: 'b', name: 'old-menu.pdf' }] },
                })}
            />,
        );
        const files = screen.getByTestId('files-change').textContent ?? '';
        expect(files).toContain('Reads price-list.pdf');
        expect(files).toContain('Stops reading old-menu.pdf');
    });

    it('says who to wait for when the viewer may not publish', async () => {
        publisher.mockResolvedValue({
            data: { can_publish: false, waiting: 'Waiting for Asha or a workspace admin to publish.' },
        });
        render(<EditCard event={event(3, { step: 'Start', diff: '' })} />);
        expect((await screen.findByTestId('edit-waiting')).textContent).toBe(
            'Waiting for Asha or a workspace admin to publish.',
        );
        expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Discard' })).toBeTruthy();
    });

    it('offers Undo on a published card, and sends undo', async () => {
        const published = event(4, { step: 'Start', decided: { action: 'publish', at: '2026-10-09T06:01:00Z' } });
        const undone = event(4, { ...published.payload, undone: { by: 1, at: '2026-10-09T06:02:00Z' } });
        settle.mockResolvedValue({ data: undone });
        const onSettled = vi.fn();
        render(<EditCard event={published} onSettled={onSettled} />);
        fireEvent.click(await screen.findByTestId('edit-undo'));
        await waitFor(() => expect(onSettled).toHaveBeenCalledWith(undone));
        expect(settle).toHaveBeenCalledWith({ body: { event_id: 4, action: 'undo' } });
    });

    it('says when Undo was refused as edited elsewhere', () => {
        render(
            <EditCard
                event={event(5, {
                    step: 'Start',
                    decided: { action: 'publish' },
                    undo_refused: { kind: 'conflict', reasons: ['x'] },
                })}
            />,
        );
        expect(screen.getByTestId('undo-refused').textContent).toContain('edited elsewhere since');
    });

    it('has no Undo once undone', () => {
        render(
            <EditCard event={event(6, { step: 'Start', decided: { action: 'publish' }, undone: { at: '2026-10-09T06:02:00Z' } })} />,
        );
        expect(screen.queryByTestId('edit-undo')).toBeNull();
        expect(screen.getByText(/Undone/)).toBeTruthy();
    });
});

describe('the card, with editing_v2 off', () => {
    it('neither asks who may publish nor offers Undo', () => {
        flags.editing_v2 = false;
        render(<EditCard event={event(7, { step: 'Start', decided: { action: 'publish' } })} />);
        expect(publisher).not.toHaveBeenCalled();
        expect(screen.queryByTestId('edit-undo')).toBeNull();
    });
});

describe('the stack', () => {
    it('draws nothing for one waiting card', () => {
        const { container } = render(<EditStack pending={[event(1, { step: 'Start' })]} />);
        expect(container.innerHTML).toBe('');
    });

    it('publishes each card in turn and shows each result', async () => {
        const first = event(1, { step: 'Start' });
        const second = event(2, { step: 'End' });
        settle
            .mockResolvedValueOnce({ data: event(1, { step: 'Start', decided: { action: 'publish' } }) })
            .mockResolvedValueOnce({ error: { detail: 'This change was edited elsewhere since — open the editor.' } });
        const onSettled = vi.fn();
        render(<EditStack pending={[first, second]} onSettled={onSettled} />);
        expect(screen.getByText('2 changes waiting')).toBeTruthy();

        fireEvent.click(await screen.findByRole('button', { name: 'Publish all' }));

        const results = await screen.findByLabelText('What happened to each change');
        await waitFor(() => expect(results.querySelectorAll('li')).toHaveLength(2));
        expect(results.textContent).toContain('Start: Published');
        expect(results.textContent).toContain('End: This change was edited elsewhere since');
        expect(settle).toHaveBeenNthCalledWith(1, { body: { event_id: 1, action: 'publish' } });
        expect(settle).toHaveBeenNthCalledWith(2, { body: { event_id: 2, action: 'publish' } });
        expect(onSettled).toHaveBeenCalledTimes(1);
    });

    it('discards all, card by card', async () => {
        settle.mockImplementation(({ body }: { body: { event_id: number } }) =>
            Promise.resolve({ data: event(body.event_id, { decided: { action: 'discard' } }) }),
        );
        render(<EditStack pending={[event(1, { step: 'Start' }), event(2, { step: 'End' })]} />);
        fireEvent.click(screen.getByRole('button', { name: 'Discard all' }));
        await waitFor(() => expect(settle).toHaveBeenCalledTimes(2));
        expect(settle).toHaveBeenCalledWith({ body: { event_id: 2, action: 'discard' } });
    });

    it('a member sees who to wait for instead of Publish all', async () => {
        publisher.mockResolvedValue({ data: { can_publish: false, waiting: 'Waiting for a workspace admin to publish.' } });
        render(<EditStack pending={[event(1, { step: 'Start' }), event(2, { step: 'End' })]} />);
        expect(await screen.findByText('Waiting for a workspace admin to publish.')).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Publish all' })).toBeNull();
    });
});
