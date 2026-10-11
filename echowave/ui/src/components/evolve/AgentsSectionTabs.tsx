'use client';

/**
 * "My agents · Skills" under the Agents header, while `evolve_skills` is on.
 * Off, it renders nothing and the Agents page is exactly what it was.
 */

import { PageTabs } from '@/components/layout/PageHeader';
import { AGENTS_TABS } from '@/components/layout/SectionTabs';
import { useFeature } from '@/lib/features';

export function AgentsSectionTabs() {
    const on = useFeature('evolve_skills');
    if (!on) return null;
    return <PageTabs tabs={AGENTS_TABS} />;
}
