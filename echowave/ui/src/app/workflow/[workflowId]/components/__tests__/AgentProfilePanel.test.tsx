/**
 * The bot as a teammate: its skills by name, what it reads, what the
 * business has confirmed, drawn from the graph it runs.
 */
import { render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

const tools = vi.hoisted(() => vi.fn());
const memory = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    listToolsApiV1ToolsGet: tools,
    readMemoryApiV1OrganisationMemoryGet: memory,
    setStatusApiV1OrganisationMemoryFactIdStatusPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import type { FlowNode } from '@/components/flow/types';

import { AgentProfilePanel, documentCountOf, skillIdsOf } from '../AgentProfilePanel';

const NODES = [
    { id: 'g', type: 'globalNode', position: { x: 0, y: 0 }, data: { name: 'Rules', prompt: 'x' } },
    { id: 'a', type: 'agentNode', position: { x: 0, y: 0 }, data: { name: 'Book', prompt: 'x', tool_uuids: ['t1', 't2'], document_uuids: ['d1'] } },
    { id: 'b', type: 'agentNode', position: { x: 0, y: 0 }, data: { name: 'Confirm', prompt: 'x', tool_uuids: ['t1'], document_uuids: ['d1', 'd2'] } },
] as unknown as FlowNode[];

describe('what the graph says', () => {
    it('names each skill once and counts documents once', () => {
        expect(skillIdsOf(NODES)).toEqual(['t1', 't2']);
        expect(documentCountOf(NODES)).toBe(2);
    });
});

describe('the panel', () => {
    it('shows skills by name, knowledge, and what the business confirmed', async () => {
        tools.mockResolvedValue({
            data: [
                { tool_uuid: 't1', name: 'Check availability', category: 'google_calendar' },
                { tool_uuid: 't2', name: 'Book slot', category: 'rate_table' },
            ],
        });
        memory.mockResolvedValue({ data: { facts: [{ id: 1, key: 'Opening hours', value: '9 to 6', status: 'confirmed' }], gaps: [] } });
        render(<AgentProfilePanel workflowId={3} name="Narayani Dental front desk" nodes={NODES} />);
        expect(screen.getByText('Narayani Dental front desk')).toBeTruthy();
        expect(screen.getByText('2 steps')).toBeTruthy();
        // A calendar is outside software; a rate lookup is the bot's own.
        // An integration is named for the app, not the step: the owner set
        // up Google Calendar, and "Check availability" is what the bot does
        // on it. The step is the tooltip.
        const integrations = await screen.findByRole('list', { name: 'Integrations & tools' });
        expect(integrations.textContent).toBe('Google Calendar');
        expect(integrations.querySelector('li')?.getAttribute('title')).toBe('Check availability');
        expect(screen.getByRole('list', { name: 'Skills' }).textContent).toContain('Book slot');
        expect(screen.getByText(/2 documents of its own/)).toBeTruthy();
        expect(await screen.findByText('9 to 6')).toBeTruthy();
    });

    it('two steps on one app are one chip', async () => {
        tools.mockResolvedValue({
            data: [
                { tool_uuid: 't1', name: 'Check availability', category: 'google_calendar' },
                { tool_uuid: 't2', name: 'Book appointment', category: 'google_calendar' },
            ],
        });
        memory.mockResolvedValue({ data: { facts: [], gaps: [] } });
        render(<AgentProfilePanel workflowId={3} name="Bot" nodes={NODES} />);
        const integrations = await screen.findByRole('list', { name: 'Integrations & tools' });
        expect(integrations.querySelectorAll('li').length).toBe(1);
        expect(integrations.querySelector('li')?.getAttribute('title')).toBe('Check availability, Book appointment');
    });

    it('offers a door when there are no skills', () => {
        tools.mockResolvedValue({ data: [] });
        memory.mockResolvedValue({ data: { facts: [], gaps: [] } });
        render(<AgentProfilePanel workflowId={3} name="Bot" nodes={[]} />);
        expect(screen.getByRole('link', { name: 'Add a skill' }).getAttribute('href')).toBe('/workflow/3/tools');
    });
});
