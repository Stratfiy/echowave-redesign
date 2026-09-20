import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../WorkflowTable', () => ({ WorkflowTable: () => <div>Existing agent management</div> }));
vi.mock('../folders/FolderSection', () => ({ FolderSection: () => <div>Existing group management</div> }));

import { AgentFolderView } from '../folders/AgentFolderView';

const workflows = [{ id: 39, name: 'Clinic front desk', status: 'active', is_live: true, total_runs: 7, created_at: '2026-09-20' }];

describe('agent directory', () => {
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
