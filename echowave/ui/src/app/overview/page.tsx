"use client";

/**
 * The first screen after signing in, and the only one whose job is to get
 * somebody to a working agent rather than to show them something.
 *
 * It used to open with "Open source alternative to Vapi — help us support the
 * project by giving us a star on GitHub", gated on `provider !== 'stack'`.
 * That condition is true for every local-auth deployment, which is what
 * production runs — so a paying customer's first impression was a request for
 * a GitHub star on a repository they have no relationship with, above a
 * comparison to a competitor. Both are gone. A customer is not a contributor.
 *
 * What replaced them is the order the work actually happens in: describe the
 * business, hear it on a call, put it on a number. The chat panel stays at the
 * top because it is the shortest path to a working agent, and the cards below
 * it are the next two steps rather than a directory of subsystems.
 */

import { HomeAboveTheFold } from '@/components/home/HomeAboveTheFold';
import { LazySection } from '@/components/home/LazySection';
import { PageBody, PageHeader } from '@/components/layout/PageHeader';
import { OverviewDashboard } from '@/components/overview/OverviewDashboard';
import { useAuth } from '@/lib/auth';

export default function OverviewPage() {
    const { user } = useAuth();
    const firstName = user?.displayName?.split(' ')[0];

    return (
        <>
            <PageHeader
                title="Home"
                // The greeting moved into the body, where it can say what
                // actually happened rather than what the page contains.
                description="Your team, and what it has been doing."
            />
            {/* The one screen that keeps a reading-width column inside the body.
                Everything below is a chat composer and two prose cards; run
                full-bleed at 1440 the input alone would be over a metre of
                line, which is worse than the gutter the shell exists to remove. */}
            <PageBody className="space-y-6">
                {/* What happened, in sentences: the greeting, the composer,
                    chips built from this account's own state, and the team. */}
                <HomeAboveTheFold firstName={firstName} />
                {/* How it is trending, in charts — and not before somebody
                    scrolls to them. These are four analytics endpoints and a
                    charting library; paying for them on first paint made the
                    screen that decides whether the product feels alive the
                    slowest one in it. */}
                <LazySection>
                    <OverviewDashboard firstName={firstName} />
                </LazySection>
            </PageBody>
        </>
    );
}
