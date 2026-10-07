/**
 * Screen 12: the record shows its summary, decisions and transcript in tabs,
 * gaps where they happened, partial and failed honestly, and every suggested
 * action with its source; an action becomes real only through its own card,
 * approved with the version shown. Deleting says what happens to its tasks.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeetingRecord } from '@/client/types.gen';

import { MeetingActionRow } from '../MeetingActionRow';
import { MeetingRecordView, processingLine } from '../MeetingRecordView';
import { interleave, TranscriptView } from '../TranscriptView';
import { action, record } from './fixtures';

const api = vi.hoisted(() => ({
    get: vi.fn(),
    review: vi.fn(),
    settle: vi.fn(),
    edit: vi.fn(),
    preview: vi.fn(),
    remove: vi.fn(),
    retry: vi.fn(),
}));
vi.mock('@/client/sdk.gen', () => ({
    getMeetingApiV1MeetingsMeetingIdGet: api.get,
    reviewActionApiV1MeetingsMeetingIdActionsItemIdReviewPost: api.review,
    settleActionApiV1MeetingsMeetingIdActionsItemIdSettlePost: api.settle,
    editActionApiV1MeetingsMeetingIdActionsItemIdPut: api.edit,
    deletionPreviewApiV1MeetingsMeetingIdDeletionGet: api.preview,
    deleteMeetingApiV1MeetingsMeetingIdDelete: api.remove,
    retryMeetingApiV1MeetingsMeetingIdRetryPost: api.retry,
    exportMeetingApiV1MeetingsMeetingIdExportGet: vi.fn(),
    readAgainApiV1MeetingsMeetingIdReadPost: vi.fn(),
    renameMeetingApiV1MeetingsMeetingIdPatch: vi.fn(),
    correctTranscriptApiV1MeetingsMeetingIdTranscriptSeqPut: vi.fn(),
    stopMeetingApiV1MeetingsMeetingIdStopPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
const push = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
});

describe('the transcript', () => {
    it('shows a gap where it happened, before the part after it', () => {
        const r = record();
        const rows = interleave(r.transcript, r.breaks);
        expect(rows.map((row) => row.kind)).toEqual(['part', 'break', 'part']);
        render(<TranscriptView parts={r.transcript} breaks={r.breaks} onRetry={() => {}} />);
        expect(screen.getByTestId('transcript-gap').textContent).toContain('Gap: This part never arrived');
        expect(screen.getByText(/Not transcribed: Sarvam failed./)).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    });

    it('a pause reads as a pause, not a gap', () => {
        render(
            <TranscriptView
                parts={[]}
                breaks={[{ kind: 'pause', reason: null, reason_label: 'Paused', at_ms: 1000, duration_ms: 120000 }]}
            />,
        );
        expect(screen.getByTestId('transcript-pause').textContent).toContain('Paused (2 min)');
        expect(screen.queryByTestId('transcript-gap')).toBeNull();
    });
});

describe('a suggested action', () => {
    const rowWith = (overrides = {}, onRecord = vi.fn()) =>
        render(
            <ul>
                <MeetingActionRow meetingId="m1" action={action(overrides)} startOf={() => 0} onRecord={onRecord} onShowSource={vi.fn()} />
            </ul>,
        );

    it('shows its source, owner, full date and confidence before anything happens', () => {
        rowWith();
        expect(screen.getByText(/I will send the deck by Friday/)).toBeTruthy();
        expect(screen.getByText('Priya')).toBeTruthy();
        expect(screen.getByText(/2026/)).toBeTruthy();
        expect(screen.getByText(/a suggestion until you confirm/)).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Review' })).toBeTruthy();
    });

    it('names what is missing and flags an unfound quote', () => {
        rowWith({ owner_name: null, due_at: null, missing: ['owner', 'due'], source_found: false });
        expect(screen.getByText('Not named')).toBeTruthy();
        expect(screen.getByText('No time said')).toBeTruthy();
        expect(screen.getByText(/not found in the transcript/)).toBeTruthy();
    });

    it('Review shows the exact card; Approve sends the version shown', async () => {
        const proposed = action({
            card: {
                event_id: 77,
                state: 'proposed',
                version: 'v-hash',
                revision: 1,
                label: 'Add a task: Send the deck to the client',
                effect: 'Adds one task to the task board in Acme. Nothing is sent to anyone: no email, message or calendar invitation.',
                fires_at: null,
                error: null,
                done_note: null,
                task_id: null,
                args: { task: 'Send the deck to the client', owner_name: 'Priya', due_at: '2026-10-09T11:30:00+00:00', due_text: 'by Friday', excerpt: 'I will send the deck by Friday' },
            },
        });
        api.settle.mockResolvedValue({ data: record({ actions: [proposed] }) });
        rowWith(proposed);
        const card = screen.getByTestId('action-preview');
        expect(within(card).getByText(/no email, message or calendar invitation/)).toBeTruthy();
        fireEvent.click(within(card).getByRole('button', { name: 'Approve' }));
        await waitFor(() => expect(api.settle).toHaveBeenCalled());
        expect(api.settle.mock.calls[0][0]).toEqual({ path: { meeting_id: 'm1', item_id: 11 }, body: { verb: 'confirm', version: 'v-hash' } });
    });

    it('Review asks the server for a card; nothing is approved by it', async () => {
        api.review.mockResolvedValue({ data: record() });
        const onRecord = vi.fn();
        rowWith({}, onRecord);
        fireEvent.click(screen.getByRole('button', { name: 'Review' }));
        await waitFor(() => expect(onRecord).toHaveBeenCalled());
        expect(api.settle).not.toHaveBeenCalled();
    });

    it('armed offers Undo; done links to the task and can be taken back', () => {
        const base = { event_id: 1, version: null, revision: 1, label: 'x', effect: null, fires_at: null, error: null, done_note: null, args: {} };
        const { unmount } = rowWith({ card: { ...base, state: 'armed', task_id: null } });
        expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
        unmount();
        rowWith({ card: { ...base, state: 'done', task_id: 5 } });
        expect(screen.getByText('Added to the task board')).toBeTruthy();
        expect(screen.getByRole('link', { name: 'Open the task' }).getAttribute('href')).toBe('/tasks/5');
        expect(screen.getByRole('button', { name: 'Take back' })).toBeTruthy();
    });

    it('an unknown outcome says not to add it again', () => {
        rowWith({ card: { event_id: 1, state: 'outcome_unknown', version: null, revision: 1, label: 'x', effect: null, fires_at: null, error: null, done_note: null, task_id: null, args: {} } });
        expect(screen.getByText(/Please do not add it again/)).toBeTruthy();
    });

    it('edits owner, task and time before confirming', async () => {
        api.edit.mockResolvedValue({ data: record() });
        rowWith();
        fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
        fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'Meera' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(api.edit).toHaveBeenCalled());
        expect(api.edit.mock.calls[0][0].body.owner_name).toBe('Meera');
    });
});

describe('the record', () => {
    it('shows summary, partial status with retry, and returns to its chat', async () => {
        api.get.mockResolvedValue({ data: record({ status: 'partial', status_reason: '1 part could not be transcribed; 1 gap in the capture.' }) });
        render(<MeetingRecordView meetingId="m1" />);
        expect(await screen.findByText('Launch sync')).toBeTruthy();
        expect(screen.getByTestId('meeting-status').textContent).toBe('Partial');
        expect(screen.getByText(/Partial: 1 part could not be transcribed/)).toBeTruthy();
        expect(screen.getByText('Priya will send the deck.')).toBeTruthy();
        expect(screen.getByRole('link', { name: /Return to chat/ }).getAttribute('href')).toBe('/overview?thread=t-9');
        expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Summary', 'Decisions', 'Transcript']);
        // The actions are on the page (below the summary on a phone, beside it when wide).
        expect(screen.getAllByText('Send the deck to the client').length).toBeGreaterThan(0);
    });

    it('a meeting that is not yours is not here', async () => {
        api.get.mockResolvedValue({ error: { detail: 'Meeting not found' }, response: { status: 404 } });
        render(<MeetingRecordView meetingId="m1" />);
        expect(await screen.findByText('This meeting is not here')).toBeTruthy();
    });

    it('processing says which stage it is at', () => {
        const busy = record({
            status: 'processing',
            transcript: [
                { seq: 0, start_ms: 0, end_ms: 1, status: 'done', text: 'a', original_text: null, corrected: false, error: null, has_action_cue: false },
                { seq: 1, start_ms: 1, end_ms: 2, status: 'pending', text: '', original_text: null, corrected: false, error: null, has_action_cue: false },
            ],
        });
        expect(processingLine(busy)).toBe('Transcribing: 1 of 2 parts done');
        expect(processingLine(record({ status: 'processing', transcript: [] }))).toBe('Writing the summary…');
    });

    it('delete explains the linked tasks and can cancel them', async () => {
        api.get.mockResolvedValue({ data: record() });
        api.preview.mockResolvedValue({
            data: {
                linked_tasks: [{ task_id: 5, title: 'Send the deck to the client', status: 'todo', can_cancel: true }],
                waiting_cards: 0,
                memory: 'Nothing from this meeting was saved to memory, so there is nothing to forget.',
                cards_note: '',
            },
        });
        api.remove.mockResolvedValue({ data: { deleted: true, tasks_cancelled: 1, tasks_kept: 0 } });
        render(<MeetingRecordView meetingId="m1" />);
        fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
        expect(await screen.findByText('Delete this meeting?')).toBeTruthy();
        expect(screen.getByText(/Nothing from this meeting was saved to memory/)).toBeTruthy();
        fireEvent.click(screen.getByLabelText(/Also cancel the tasks/));
        fireEvent.click(screen.getByRole('button', { name: 'Delete meeting' }));
        await waitFor(() => expect(api.remove).toHaveBeenCalled());
        expect(api.remove.mock.calls[0][0]).toEqual({ path: { meeting_id: 'm1' }, query: { cancel_tasks: true } });
        await waitFor(() => expect(push).toHaveBeenCalledWith('/meetings'));
    });

    it('a followed excerpt opens the transcript at its part', async () => {
        api.get.mockResolvedValue({ data: record() as MeetingRecord });
        Element.prototype.scrollIntoView = vi.fn();
        render(<MeetingRecordView meetingId="m1" />);
        const quotes = await screen.findAllByRole('button', { name: /I will send the deck by Friday/ });
        fireEvent.click(quotes[0]);
        await waitFor(() => expect(document.getElementById('part-0')?.className).toContain('ring-2'));
    });
});
