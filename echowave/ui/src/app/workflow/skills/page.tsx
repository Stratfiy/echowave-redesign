'use client';

/**
 * Agents → Skills (evolve_skills). The skills this workspace keeps, each
 * explained in full, with its versions and the evidence behind them. Not a
 * new destination: a tab of Agents, beside My agents.
 */

import Link from 'next/link';

import { AgentsSectionTabs } from '@/components/evolve/AgentsSectionTabs';
import { SkillsTab } from '@/components/evolve/SkillsTab';
import { PageBody, PageHeader } from '@/components/layout/PageHeader';
import { useFeature, useFeaturesSettled } from '@/lib/features';

export default function AgentSkillsPage() {
    const on = useFeature('evolve_skills');
    const settled = useFeaturesSettled();
    return (
        <>
            <PageHeader
                title="Your agents"
                description="The skills your agents use, how each one works, and how it has changed."
            />
            <AgentsSectionTabs />
            <PageBody>
                {on ? (
                    <SkillsTab />
                ) : settled ? (
                    <p className="text-sm text-muted-foreground">
                        Skills are in the{' '}
                        <Link href="/marketplace/skills" className="underline underline-offset-4">
                            Marketplace
                        </Link>
                        .
                    </p>
                ) : null}
            </PageBody>
        </>
    );
}
