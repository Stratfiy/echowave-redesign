import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../WorkflowTable', () => ({ WorkflowTable: () => <div>Existing agent management</div> }));
vi.mock('../folders/FolderSection', () => ({ FolderSection: () => <div>Existing group management</div> }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
const roster = vi.hoisted(() => ({ members: [] as Record<string, unknown>[] }));
vi.mock('@/client/sdk.gen', () => ({
    teamStatusApiV1TeamStatusGet: () => Promise.resolve({ data: { hours: 24, members: roster.members } }),
}));

import { AgentFolderView } from '../folders/AgentFolderView';

const workflows = [{ id: 39, name: 'Clinic front desk', status: 'active', is_live: true, total_runs: 7, created_at: '2026-09-20' }];

const member = (workflow_id: number, name: string, tone: string, status: string) => ({
    workflow_id, workflow_uuid: null, name, is_live: tone !== 'paused', status, tone, at: null,
    calls: 3, answered: 2, outcomes: 1, failures: 0, last_action: null, last_line: 'Booked Ravi for 4pm',
});

describe('agent directory', () => {
    it('shows what each agent is doing and filters by state', async () => {
        roster.members = [
            member(39, 'Clinic front desk', 'working', 'Answered 2 calls in the last hour'),
            member(40, 'Collections', 'attention', 'Could not reach the sheet'),
        ];
        render(
            <AgentFolderView
                workflows={[...workflows, { id: 40, name: 'Collections', status: 'active', is_live: true, total_runs: 2, created_at: '2026-09-21' }]}
                folders={[]}
            />,
        );
        expect(await screen.findByText('Answered 2 calls in the last hour')).toBeTruthy();
        expect(screen.getAllByText(/Booked Ravi for 4pm/).length).toBe(2);
        // The agent that needs you comes first.
        const cards = screen.getAllByTestId(/agent-card-/);
        expect(cards[0].getAttribute('data-testid')).toBe('agent-card-40');
        fireEvent.click(screen.getByRole('button', { name: /Needs you 1/ }));
        expect(screen.queryByText('Answered 2 calls in the last hour')).toBeNull();
        expect(screen.getByText('Could not reach the sheet')).toBeTruthy();
        expect(screen.getByRole('link', { name: 'Message Collections' }).getAttribute('href')).toBe('/workflow/40/thread');
        roster.members = [];
    });

    it('returns keyboard focus to the card when its profile closes', async () => {
        render(<AgentFolderView workflows={workflows} folders={[]} />);
        const card = screen.getByRole('button', { name: 'View Clinic front desk' });
        card.focus();
        fireEvent.click(card);
        fireEvent.click(screen.getByRole('button', { name: 'Close' }));
        await waitFor(() => expect(document.activeElement).toBe(card));
    });

    it('separates messaging from editing and links to the selected agent history', () => {
        render(<AgentFolderView workflows={workflows} folders={[]} />);
        fireEvent.click(screen.getByRole('button', { name: 'View Clinic front desk' }));
        expect(screen.getByRole('link', { name: 'Message' }).getAttribute('href')).toBe('/workflow/39/thread');
        expect(screen.getByRole('link', { name: 'Edit agent' }).getAttribute('href')).toBe('/workflow/39');
        expect(screen.getByRole('link', { name: /View activity/ }).getAttribute('href')).toBe('/workflow/39/runs');
        fireEvent.click(screen.getByRole('button', { name: 'Manage status, groups and archive in List' }));
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(screen.getByText('Existing agent management')).toBeTruthy();
    });

    it('retains group management in the list view', () => {
        render(<AgentFolderView workflows={workflows} folders={[]} />);
        fireEvent.click(screen.getByRole('button', { name: 'List' }));
        expect(screen.getByText('Existing agent management')).toBeTruthy();
    });
});
