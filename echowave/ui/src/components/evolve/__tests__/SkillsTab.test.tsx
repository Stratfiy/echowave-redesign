/**
 * Agents → Skills: each card answers the five questions, shows its versions
 * and the evidence behind them, and rolls back or adds to an agent in one
 * press. The tabs appear only while evolve_skills is on.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
    tab: vi.fn(),
    workflows: vi.fn(),
    rollback: vi.fn(),
    attach: vi.fn(),
}));
const flags = vi.hoisted(() => ({ on: true }));

vi.mock('@/client/sdk.gen', () => ({
    skillsTabApiV1EvolveSkillsGet: api.tab,
    getWorkflowsApiV1WorkflowFetchGet: api.workflows,
    rollbackSkillApiV1EvolveSkillsSlugRollbackPost: api.rollback,
    attachSkillApiV1EvolveSkillsSlugAgentsPost: api.attach,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({ useFeature: () => flags.on, useFeaturesSettled: () => true }));
vi.mock('next/navigation', () => ({ usePathname: () => '/workflow/skills' }));

import { AgentsSectionTabs } from '../AgentsSectionTabs';
import { SkillsTab, versionLine } from '../SkillsTab';

const CARD = {
    slug: 'brand-voice',
    title: 'Brand Voice',
    emoji: '',
    division: 'Marketing',
    own: false,
    installed: true,
    explain: {
        accomplish: 'Build a writing style profile from real posts.',
        example: 'the user wants content in a specific voice',
        needs: ['App access: LinkedIn'],
        produces: ['A profile to reuse'],
        external_actions: {
            can_act: false,
            sentence: 'No, not by itself: a skill is a procedure.',
            mentions: [],
        },
        enabled_on: ['Decibyl chat', 'Front desk'],
        wont_do: [],
    },
    on_agents: [{ id: 3, name: 'Front desk' }],
    active_version: 2,
    versions: [
        { id: 10, slug: 'brand-voice', version: 1, status: 'rolled_back', origin: 'person', lessons: ['Old habit'] },
        {
            id: 11,
            slug: 'brand-voice',
            version: 2,
            status: 'published',
            origin: 'learned',
            lessons: ["Ask for the caller's timezone before booking"],
        },
    ],
    improvement: {
        version: 2,
        related: { n: 3, baseline_passed: 0, candidate_passed: 3 },
        unrelated: { n: 4, regressions: 0 },
        cost: { model_calls: 14, tokens: 900 },
        evidence: 2,
    },
    experience: { failure: 2 },
};

beforeEach(() => {
    vi.clearAllMocks();
    flags.on = true;
    api.tab.mockResolvedValue({ data: { skills: [CARD], max_per_bot: 8 } });
    api.workflows.mockResolvedValue({
        data: [
            { id: 3, name: 'Front desk' },
            { id: 7, name: 'Collections' },
        ],
    });
    api.rollback.mockResolvedValue({ data: { slug: 'brand-voice', rolled_back: 2, active: 1 } });
    api.attach.mockResolvedValue({ data: { slug: 'brand-voice', workflow_id: 7 } });
});

describe('SkillsTab', () => {
    it('answers the five questions on every card', async () => {
        render(<SkillsTab />);
        expect(await screen.findByText('Brand Voice')).toBeTruthy();
        expect(screen.getByText('What it helps with')).toBeTruthy();
        expect(screen.getByText(/Build a writing style profile/)).toBeTruthy();
        expect(screen.getByText(/For example: the user wants content/)).toBeTruthy();
        expect(screen.getByText('What it needs')).toBeTruthy();
        expect(screen.getByText('App access: LinkedIn')).toBeTruthy();
        expect(screen.getByText('What it produces')).toBeTruthy();
        expect(screen.getByText('Can it act outside Decibyl?')).toBeTruthy();
        expect(screen.getByText(/a skill is a procedure/)).toBeTruthy();
        expect(screen.getByText('Where it is on')).toBeTruthy();
        expect(screen.getByText('Decibyl chat')).toBeTruthy();
    });

    it('shows the evidence of improvement and the version history', async () => {
        render(<SkillsTab />);
        const evidence = await screen.findByTestId('skill-improvement');
        expect(evidence.textContent).toMatch(/from 0 to 3\s+of 3 right/);
        expect(evidence.textContent).toMatch(/no unrelated task got worse/);
        expect(evidence.textContent).toMatch(/14 model calls/);
        expect(screen.getByText('Version 2 in use · Marketing')).toBeTruthy();
        expect(screen.getByText('Version 2 · Learned from work · Published')).toBeTruthy();
        expect(screen.getByText('Version 1 · Written by a person · Rolled back')).toBeTruthy();
    });

    it('rolls back in one press', async () => {
        render(<SkillsTab />);
        fireEvent.click(await screen.findByLabelText('Roll back Brand Voice'));
        await waitFor(() =>
            expect(api.rollback).toHaveBeenCalledWith({ path: { slug: 'brand-voice' }, body: {} }),
        );
        await waitFor(() => expect(api.tab).toHaveBeenCalledTimes(2));
    });

    it('adds it to an agent that does not have it yet', async () => {
        render(<SkillsTab />);
        await waitFor(() => expect(api.workflows).toHaveBeenCalled());
        fireEvent.click(await screen.findByLabelText('Add Brand Voice to an agent'));
        // Front desk already has it, so only Collections is offered.
        const group = screen.getByRole('group', { name: 'Agents' });
        expect(group.textContent).toBe('Collections');
        fireEvent.click(screen.getByRole('button', { name: 'Collections' }));
        await waitFor(() =>
            expect(api.attach).toHaveBeenCalledWith({
                path: { slug: 'brand-voice' },
                body: { workflow_id: 7 },
            }),
        );
    });

    it('says where to find skills when there are none', async () => {
        api.tab.mockResolvedValue({ data: { skills: [], max_per_bot: 8 } });
        render(<SkillsTab />);
        expect(await screen.findByText(/remember this as my way of doing it/)).toBeTruthy();
    });

    it('names each version by who made it', () => {
        expect(
            versionLine({ id: 1, slug: 's', version: 3, status: 'offered', origin: 'remembered' }),
        ).toBe('Version 3 · Remembered from a conversation · Waiting for approval');
    });
});

describe('AgentsSectionTabs', () => {
    it('shows My agents and Skills while the flag is on', () => {
        render(<AgentsSectionTabs />);
        expect(screen.getByText('My agents')).toBeTruthy();
        expect(screen.getByText('Skills').closest('a')?.getAttribute('aria-current')).toBe('page');
    });

    it('renders nothing while the flag is off', () => {
        flags.on = false;
        const { container } = render(<AgentsSectionTabs />);
        expect(container.innerHTML).toBe('');
    });
});
