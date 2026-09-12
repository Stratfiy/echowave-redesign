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

import { PageBody, PageHeader } from '@/components/layout/PageHeader';
import { OverviewDashboard } from '@/components/overview/OverviewDashboard';
import { TeamPanel } from '@/components/team/TeamPanel';
import { useAuth } from '@/lib/auth';

export default function OverviewPage() {
    const { user } = useAuth();
    const firstName = user?.displayName?.split(' ')[0];

    return (
        <>
            <PageHeader
                title="Overview"
                description={
                    firstName
                        ? `Welcome back, ${firstName}. Calls, answer rate and credits at a glance.`
                        : 'Calls, answer rate and credits at a glance.'
                }
            />
            {/* The one screen that keeps a reading-width column inside the body.
                Everything below is a chat composer and two prose cards; run
                full-bleed at 1440 the input alone would be over a metre of
                line, which is worse than the gutter the shell exists to remove. */}
            <PageBody className="space-y-6">
                {/* Who is working, before how it is trending. The team reads
                    as sentences — "9 calls, 6 answered, 4 bookings" — and the
                    charts below answer the question those sentences raise.
                    Renders nothing until there is an agent, so a new account
                    still opens on the door rather than on an empty list. */}
                <TeamPanel />
                {/* A door until the first call, a dashboard after it. The
                    builder chat, the next steps and the docs links moved inside
                    the dashboard's empty state so this page has one job at a
                    time. */}
                <OverviewDashboard firstName={firstName} />
            </PageBody>
        </>
    );
}
