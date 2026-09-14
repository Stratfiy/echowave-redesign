/**
 * The memory list, with forget and undo.
 *
 * Guarded: a bot's list asks for its own scope and marks the bot's own
 * facts; Forget sets the status to rejected and the row says so with Undo;
 * Undo puts the status back to what it was.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const read = vi.hoisted(() => vi.fn());
const setStatus = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    readMemoryApiV1OrganisationMemoryGet: read,
    setStatusApiV1OrganisationMemoryFactIdStatusPost: setStatus,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import { MemoryList } from '../MemoryList';

const fact = (over: Record<string, unknown>) => ({
    id: 1,
    kind: 'fact',
    subject: 'self',
    key: 'Opening hours',
    value: '9 to 6',
    status: 'confirmed',
    times_seen: 1,
    first_seen_at: null,
    last_seen_at: null,
    source_run_id: null,
    workflow_id: null,
    ...over,
});

beforeEach(() => {
    read.mockReset();
    setStatus.mockReset();
});

describe('a bot list', () => {
    it("asks for the bot's scope and marks its own facts", async () => {
        read.mockResolvedValue({
            data: { facts: [fact({}), fact({ id: 2, key: 'Language', value: 'Hindi', workflow_id: 3 })], gaps: [] },
        });
        render(<MemoryList workflowId={3} botName="Front desk" />);
        expect(await screen.findByText('9 to 6')).toBeTruthy();
        expect(read.mock.calls[0][0].query).toEqual({ workflow_id: 3 });
        expect(screen.getByText('Front desk only')).toBeTruthy();
    });
});

describe('forgetting', () => {
    it('rejects the fact, says so with Undo, and Undo puts it back', async () => {
        read.mockResolvedValue({ data: { facts: [fact({})], gaps: [] } });
        setStatus.mockResolvedValue({ data: fact({ status: 'rejected' }) });
        render(<MemoryList />);
        fireEvent.click(await screen.findByRole('button', { name: 'Forget Opening hours' }));
        await waitFor(() => expect(setStatus).toHaveBeenCalled());
        expect(setStatus.mock.calls[0][0]).toEqual({ path: { fact_id: 1 }, body: { status: 'rejected' } });
        const undo = await screen.findByRole('button', { name: /Forgotten · Undo/ });
        expect(screen.queryByText('9 to 6')).toBeNull();
        fireEvent.click(undo);
        await waitFor(() => expect(setStatus).toHaveBeenCalledTimes(2));
        expect(setStatus.mock.calls[1][0].body).toEqual({ status: 'confirmed' });
        expect(await screen.findByText('9 to 6')).toBeTruthy();
    });

    it('shows a refusal', async () => {
        read.mockResolvedValue({ data: { facts: [fact({})], gaps: [] } });
        setStatus.mockResolvedValue({ error: { detail: 'Not found' } });
        render(<MemoryList />);
        fireEvent.click(await screen.findByRole('button', { name: 'Forget Opening hours' }));
        expect(await screen.findByRole('alert')).toBeTruthy();
    });
});
