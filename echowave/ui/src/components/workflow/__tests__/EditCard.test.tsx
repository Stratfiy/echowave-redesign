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

    it('shows a greeting change, labelled, before and after', () => {
        render(
            <EditCard
                event={event({
                    payload: {
                        step: 'Start',
                        diff: '',
                        greetings: [
                            {
                                step: 'Start',
                                node_id: 's',
                                old: 'Namaste, City Dental.',
                                new: 'Namaste, Sharma Dental.',
                            },
                        ],
                    },
                })}
            />,
        );
        const change = screen.getByTestId('greeting-change');
        expect(change.textContent).toContain('Greeting · Start');
        expect(change.textContent).toContain('Namaste, City Dental.');
        expect(change.textContent).toContain('Namaste, Sharma Dental.');
        // No prompt diff to draw.
        expect(screen.queryByLabelText('What changes')).toBeNull();
    });

    it('a card whose Publish was refused keeps the reasons and stays open', () => {
        render(
            <EditCard
                event={event({
                    payload: {
                        step: 'Find a slot',
                        diff: DIFF,
                        refused: {
                            kind: 'acceptable_use',
                            reasons: ['Deceiving the people it talks to'],
                        },
                    },
                })}
            />,
        );
        const refused = screen.getByTestId('edit-refused');
        expect(refused.textContent).toContain('acceptable use policy');
        expect(refused.textContent).toContain('Deceiving the people it talks to');
        expect(screen.getByRole('button', { name: 'Publish' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Discard' })).toBeTruthy();
    });

    it('a card whose step was edited elsewhere since points at the editor', () => {
        render(
            <EditCard
                event={event({
                    payload: {
                        step: 'Find a slot',
                        diff: DIFF,
                        refused: {
                            kind: 'conflict',
                            reasons: ['This change was edited elsewhere since — open the editor.'],
                        },
                    },
                })}
            />,
        );
        const refused = screen.getByTestId('edit-refused');
        expect(refused.textContent).toContain('edited elsewhere since');
        expect(screen.getByTestId('open-editor').getAttribute('href')).toBe('/workflow/3');
        // Discard still takes the card's own change out.
        expect(screen.getByRole('button', { name: 'Discard' })).toBeTruthy();
    });

    it('a conflict on the click links to the editor too', async () => {
        settle.mockResolvedValue({
            error: { detail: 'This change was edited elsewhere since — open the editor.' },
        });
        render(<EditCard event={event()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
        expect((await screen.findByRole('alert')).textContent).toContain('edited elsewhere since');
        expect(screen.getByTestId('open-editor').getAttribute('href')).toBe('/workflow/3');
    });

    it('a card from before the update says so honestly, and can still be discarded', () => {
        render(
            <EditCard
                event={event({
                    payload: {
                        step: 'Find a slot',
                        diff: DIFF,
                        refused: {
                            kind: 'legacy',
                            reasons: [
                                "This card was made before an update and can't be applied on its own — open the editor to review it.",
                            ],
                        },
                    },
                })}
            />,
        );
        const refused = screen.getByTestId('edit-refused');
        expect(refused.textContent).toContain("made before an update and can't be applied on its own");
        expect(refused.textContent).not.toContain('edited elsewhere');
        expect(screen.getByTestId('open-editor').getAttribute('href')).toBe('/workflow/3');
        expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Discard' })).toBeTruthy();
    });

    it('a refused publish says why', async () => {
        settle.mockResolvedValue({
            error: { detail: 'This change cannot go live yet: Field required (position on node 1)' },
        });
        render(<EditCard event={event()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Publish' }));
        expect((await screen.findByRole('alert')).textContent).toContain('cannot go live yet');
        expect(screen.getByRole('button', { name: 'Publish' })).toBeTruthy();
    });

    it('a refused click is said on the card', async () => {
        settle.mockResolvedValue({ error: { detail: 'Already settled.' } });
        render(<EditCard event={event()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
        expect((await screen.findByRole('alert')).textContent).toContain('Already settled');
    });
});

describe('editing before publishing', () => {
    const changes = [{ node_id: '1', field: 'prompt', old: 'Ask for a date.', new: "Ask for the patient's name, then a date." }];
    const withChanges = (extra: Record<string, unknown> = {}) =>
        event({ payload: { step: 'Find a slot', why: 'Name first', diff: DIFF, changes, ...extra } });

    it('offers Edit, which opens the proposed text to change', () => {
        render(<EditCard event={withChanges()} />);
        expect(screen.queryByTestId('edit-fields')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        const box = screen.getByLabelText('Find a slot · instructions') as HTMLTextAreaElement;
        expect(box.value).toBe("Ask for the patient's name, then a date.");
        expect(screen.getByRole('button', { name: 'Publish edited' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: 'Publish' })).toBeNull();
    });

    it('publishes the changed text, and only what was changed', async () => {
        settle.mockResolvedValue({ data: event() });
        render(<EditCard event={withChanges()} />);
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        fireEvent.change(screen.getByLabelText('Find a slot · instructions'), {
            target: { value: 'Ask for their name, then a date.' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Publish edited' }));
        await waitFor(() =>
            expect(settle).toHaveBeenCalledWith({
                body: {
                    event_id: 9,
                    action: 'publish',
                    edits: [{ node_id: '1', field: 'prompt', new: 'Ask for their name, then a date.' }],
                },
            }),
        );
    });

    it('publishing edited text that was not changed sends no edits', async () => {
        settle.mockResolvedValue({ data: event() });
        render(<EditCard event={withChanges()} />);
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Publish edited' }));
        await waitFor(() => expect(settle).toHaveBeenCalledWith({ body: { event_id: 9, action: 'publish' } }));
    });

    it('a refusal keeps the card open with what was typed', async () => {
        settle.mockResolvedValue({ error: { detail: 'This change cannot go live yet: x' } });
        render(<EditCard event={withChanges()} />);
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        fireEvent.change(screen.getByLabelText('Find a slot · instructions'), { target: { value: 'Mine' } });
        fireEvent.click(screen.getByRole('button', { name: 'Publish edited' }));
        expect((await screen.findByRole('alert')).textContent).toContain('cannot go live yet');
        expect((screen.getByLabelText('Find a slot · instructions') as HTMLTextAreaElement).value).toBe('Mine');
    });

    it('Cancel edit goes back to the plain card', () => {
        render(<EditCard event={withChanges()} />);
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel edit' }));
        expect(screen.getByRole('button', { name: 'Publish' })).toBeTruthy();
    });

    it('a greeting is edited on its own field', () => {
        render(
            <EditCard
                event={event({
                    payload: {
                        step: 'Start',
                        diff: '',
                        greetings: [{ step: 'Start', node_id: 's', old: 'Namaste.', new: 'Namaste, Sharma Dental.' }],
                        changes: [{ node_id: 's', field: 'greeting', old: 'Namaste.', new: 'Namaste, Sharma Dental.' }],
                    },
                })}
            />,
        );
        fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
        expect(screen.getByLabelText('Start · greeting')).toBeTruthy();
    });

    it('a card with no recorded changes, or only a setting, cannot be edited', () => {
        const { unmount } = render(<EditCard event={event()} />);
        expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull();
        unmount();
        render(
            <EditCard
                event={event({
                    id: 10,
                    payload: { step: 'Escalation', diff: '', changes: [{ config: 'escalation_policy', old: '{}', new: '{"a":1}' }] },
                })}
            />,
        );
        expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull();
    });

    it('a settled edited card says it was edited, and offers nothing to press', () => {
        render(
            <EditCard
                event={withChanges({ original: { new: 'x' }, decided: { action: 'publish', at: '2026-09-14T06:01:00Z' } })}
            />,
        );
        expect(screen.getByText(/Published, as edited/)).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Edit/ })).toBeNull();
    });
});
